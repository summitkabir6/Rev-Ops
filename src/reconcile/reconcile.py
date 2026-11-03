"""Compare every metric across sources for a date range, flag variances, explain them.

Loads the data folder (idempotent) and rebuilds the model first, so it runs on its own.

Usage:
    python -m src.reconcile.reconcile --start 2026-07-05 --end 2026-11-02
    python -m src.reconcile.reconcile --dry-run
"""
import argparse
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import yaml

from src.config import REPORTS_DIR, data_dir, load_settings, warehouse_path
from src.db import connect
from src.ingest.run import ingest_all
from src.model.build import build

from .diagnostics import checks
from .metrics import METRIC_ORDER, queries

KNOWN_CASES = Path(__file__).with_name("known_cases.yml")


@dataclass
class Row:
    metric: str
    source: str
    value: float | None
    truth_value: float | None
    variance: float | None
    variance_pct: float | None
    severity: str
    is_source_of_truth: bool


@dataclass
class Cause:
    label: str
    records: int
    effect: float | None


@dataclass
class Exception_:
    metric: str
    source: str
    severity: str
    variance: float
    variance_pct: float | None
    causes: list[Cause]
    explained: float | None

    @property
    def likely_cause(self) -> Cause | None:
        return self.causes[0] if self.causes else None


def as_number(value):
    if value is None:
        return None
    return float(value) if isinstance(value, Decimal) else value


def severity(kind: str, variance: float | None, pct: float | None, thresholds: dict) -> str:
    if variance is None:
        return "n/a"
    rule = thresholds[kind]
    if variance == 0:
        return "ok"
    if pct is None or pct > rule["critical_pct"]:
        return "critical"
    if kind == "money":
        return "warn" if pct > rule["warn_pct"] else "ok"
    return "warn" if abs(variance) > rule["warn_abs"] else "ok"


def compute_metrics(con, start: date, end: date, settings: dict) -> list[Row]:
    tz = settings["timezone"]
    params = {"start": start, "end": end}
    values: dict[tuple[str, str], float | None] = {}
    for key, sql in queries(tz).items():
        values[key] = as_number(con.execute(sql, params).fetchall()[0][0])

    rows = []
    for metric in METRIC_ORDER:
        truth_source = settings["source_of_truth"][metric]
        truth = values[(metric, truth_source)]
        kind = settings["metric_kind"][metric]
        for (m, source), value in values.items():
            if m != metric:
                continue
            is_truth = source == truth_source
            if is_truth or value is None or truth is None:
                variance, pct = (0.0, 0.0) if is_truth else (None, None)
            else:
                variance = round(value - truth, 2)
                pct = round(abs(variance) / abs(truth) * 100, 2) if truth else None
            rows.append(Row(metric, source, value, truth, variance, pct,
                            severity(kind, variance, pct, settings["thresholds"]), is_truth))
    return rows


def explain(con, rows: list[Row], start: date, end: date, settings: dict) -> list[Exception_]:
    params = {"start": start, "end": end}
    metric_checks = checks(settings["timezone"])
    found = []
    for row in rows:
        if row.severity not in ("warn", "critical"):
            continue
        causes = []
        for label, sql in metric_checks[row.metric]:
            records, effect = con.execute(sql, params).fetchall()[0]
            if records:
                causes.append(Cause(label, records, as_number(effect)))
        # Largest effect first; checks that cannot be summed keep their written order.
        causes.sort(key=lambda c: -abs(c.effect) if c.effect is not None else 0)
        summable = [c.effect for c in causes if c.effect is not None]
        explained = round(sum(summable), 2) if summable else None
        found.append(Exception_(row.metric, row.source, row.severity, row.variance, row.variance_pct, causes, explained))
    return found


def documented_cases(con, settings: dict) -> list[dict]:
    with open(KNOWN_CASES, encoding="utf-8") as fh:
        cases = yaml.safe_load(fh)
    for case in cases:
        sql = case["check"].replace("__TIMEZONE__", settings["timezone"])
        case["evidence"] = con.execute(sql).fetchall()
    return cases


def volumes(con) -> list[tuple[str, int]]:
    """Row counts behind the run: raw rows per source and stream, then the conformed tables."""
    raw = con.execute("""
        SELECT source_name || ' / ' || stream, count(*) FROM (
            SELECT source_name, stream FROM raw_shopify
            UNION ALL SELECT source_name, stream FROM raw_klaviyo
            UNION ALL SELECT source_name, stream FROM raw_posthog)
        GROUP BY ALL ORDER BY 1
    """).fetchall()
    model = [(t, con.execute(f"SELECT count(*) FROM {t}").fetchall()[0][0])
             for t in ("dim_customer", "fct_order", "fct_send", "fct_funnel", "identity_review")]
    merged = con.execute("SELECT count(*) FROM dim_customer WHERE resolution = 'merged'").fetchall()[0][0]
    internal = con.execute("SELECT count(*) FROM internal_emails").fetchall()[0][0]
    return ([(f"raw: {name}", n) for name, n in raw] + [(f"model: {t}", n) for t, n in model]
            + [("model: dim_customer merged across addresses", merged), ("model: internal addresses", internal)])


def fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{value:,.0f}"


def describe(cause: Cause, variance: float) -> str:
    text = f"{cause.label} ({cause.records} records"
    if cause.effect is not None:
        share = f", {cause.effect / variance * 100:.1f}% of the variance" if variance else ""
        text += f", effect {fmt(cause.effect)}{share}"
    return text + ")"


def render(rows: list[Row], exceptions: list[Exception_], cases: list[dict], start: date, end: date,
           volume_rows: list[tuple[str, int]] = ()) -> str:
    lines = [f"# Reconciliation, {start} to {end}", ""]
    lines += ["| metric | source | value | source of truth | variance | variance % | severity |",
              "| --- | --- | ---: | --- | ---: | ---: | --- |"]
    for r in rows:
        truth = "yes" if r.is_source_of_truth else fmt(r.truth_value)
        pct = "" if r.variance_pct is None else f"{r.variance_pct:.2f}"
        lines.append(f"| {r.metric} | {r.source} | {fmt(r.value)} | {truth} | {fmt(r.variance)} | {pct} | {r.severity} |")

    lines += ["", "## Exceptions", ""]
    if not exceptions:
        lines.append("None. Every source agrees with its source of truth within threshold.")
    for e in exceptions:
        pct = "" if e.variance_pct is None else f" ({e.variance_pct:.2f}%)"
        head = f"- **{e.metric} / {e.source}** {e.severity}, off by {fmt(e.variance)}{pct}."
        if not e.causes:
            lines.append(f"{head} No check found a cause. Investigate record by record.")
            continue
        lines.append(f"{head} Most likely cause: {describe(e.likely_cause, e.variance)}.")
        for other in e.causes[1:]:
            lines.append(f"  - Also: {describe(other, e.variance)}.")
        if e.explained is not None:
            left = round(e.variance - e.explained, 2)
            lines.append(f"  - Checks explain {fmt(e.explained)} of {fmt(e.variance)}; unexplained: {fmt(left)}.")

    lines += ["", "## Documented cases", ""]
    for c in cases:
        lines.append(f"### {c['title']}")
        lines.append(f"Cause: {c['cause'].strip()}")
        lines.append("")
        for p in c.get("published") or []:
            lines.append(f"- published: {p['label']}: {fmt(p['value'])} (source: {p['source']})")
        for measure, value in c["evidence"]:
            lines.append(f"- {measure}: {fmt(as_number(value))}")
        lines.append("")

    if volume_rows:
        lines += ["## Data volumes", "", "| table | rows |", "| --- | ---: |"]
        lines += [f"| {name} | {n:,} |" for name, n in volume_rows]
    return "\n".join(lines)


def run(con, start: date, end: date) -> tuple[list[Row], list[Exception_], list[dict], str]:
    settings = load_settings()
    rows = compute_metrics(con, start, end, settings)
    exceptions = explain(con, rows, start, end, settings)
    cases = documented_cases(con, settings)
    return rows, exceptions, cases, render(rows, exceptions, cases, start, end, volumes(con))


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=settings["default_window"]["start"])
    parser.add_argument("--end", type=date.fromisoformat, default=settings["default_window"]["end"])
    parser.add_argument("--dry-run", action="store_true", help="use the warehouse built from data/sample")
    args = parser.parse_args()

    con = connect(warehouse_path(args.dry_run))
    ingest_all(con, data_dir(args.dry_run))
    build(con)
    _, _, _, text = run(con, args.start, args.end)
    print(text)
    REPORTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    prefix = "sample_" if args.dry_run else ""
    out = REPORTS_DIR / f"{prefix}reconcile_{args.start}_{args.end}_{stamp}.md"
    out.write_text(text, encoding="utf-8")
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
