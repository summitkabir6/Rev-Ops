"""The weekly report: one fixed layout, one command, one timestamped file.

    python -m src.report.weekly              real exports in data/raw, week before today
    python -m src.report.weekly --dry-run    synthetic rows in data/sample
    python -m src.report.weekly --as-of 2026-11-03

It loads whatever is in the data folder (loads are idempotent), rebuilds the model,
then reports the last complete Monday-to-Sunday week before --as-of against the week
before it. Every value is the source of truth's value; the exceptions section is the
reconciliation for the current week.
"""
import argparse
from datetime import date, datetime, timedelta

from src.config import REPORTS_DIR, data_dir, load_settings, warehouse_path
from src.db import connect
from src.ingest.run import ingest_all
from src.model.build import build
from src.reconcile.metrics import METRIC_ORDER
from src.reconcile.reconcile import compute_metrics, describe, explain, fmt

LABELS = {
    "order_count": "Orders",
    "gross_revenue": "Gross revenue",
    "refunds": "Refunds and cancellations",
    "net_revenue": "Net revenue",
    "active_customers": "Active customers",
    "quiz_email_captures": "Quiz email captures",
    "quiz_store_taps": "Quiz app-store taps",
    "capture_to_store_tap_rate": "Capture to store-tap rate (%)",
}

FRESHNESS = {
    "Shopify orders": "SELECT max(ordered_at_utc) FROM fct_order WHERE source_of_truth = 'orders_api'",
    "Klaviyo events": "SELECT max(occurred_at_utc) FROM stg_klaviyo_events",
    "PostHog quiz": "SELECT max(greatest(first_event_at_utc, email_submitted_at_utc, store_clicked_at_utc)) FROM stg_posthog_quiz_people",
}


def last_full_week(as_of: date) -> tuple[date, date]:
    """Monday to Sunday of the most recent week that ended before as_of."""
    end = as_of - timedelta(days=as_of.weekday() + 1)
    return end - timedelta(days=6), end


def truth_values(rows) -> dict[str, float | None]:
    return {r.metric: r.value for r in rows if r.is_source_of_truth}


def change(current, prior) -> str:
    if current is None or prior is None:
        return "n/a"
    diff = current - prior
    if not prior:
        return f"{fmt(diff)}"
    return f"{fmt(diff)} ({diff / prior * 100:+.1f}%)"


def render(con, as_of: date, settings: dict, dry_run: bool) -> tuple[str, date]:
    start, end = last_full_week(as_of)
    prior_start, prior_end = start - timedelta(days=7), end - timedelta(days=7)
    current_rows = compute_metrics(con, start, end, settings)
    prior = truth_values(compute_metrics(con, prior_start, prior_end, settings))
    current = truth_values(current_rows)
    exceptions = explain(con, current_rows, start, end, settings)

    lines = [
        f"# Weekly revenue and funnel report, {start} to {end}",
        "",
        f"Compared with {prior_start} to {prior_end}. Week runs Monday to Sunday, {settings['timezone']}.",
        f"Generated {datetime.now():%Y-%m-%d %H:%M}{' from synthetic sample data (--dry-run)' if dry_run else ''}.",
        "",
        "## Metrics",
        "",
        "| metric | this week | prior week | change | source of truth |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for metric in METRIC_ORDER:
        lines.append(f"| {LABELS[metric]} | {fmt(current[metric])} | {fmt(prior[metric])} | "
                     f"{change(current[metric], prior[metric])} | {settings['source_of_truth'][metric]} |")

    lines += ["", "## Exceptions", ""]
    if not exceptions:
        lines.append("None. Every source agrees with its source of truth within threshold this week.")
    for e in exceptions:
        pct = "" if e.variance_pct is None else f" ({e.variance_pct:.2f}%)"
        cause = describe(e.likely_cause, e.variance) if e.causes else "no check found a cause; investigate"
        lines.append(f"- {LABELS[e.metric]}: {e.source} is off by {fmt(e.variance)}{pct}, {e.severity}. Most likely: {cause}.")

    lines += ["", "## Data freshness", "", "| source | latest record (UTC) |", "| --- | --- |"]
    for name, sql in FRESHNESS.items():
        latest = con.execute(sql).fetchall()[0][0]
        lines.append(f"| {name} | {latest:%Y-%m-%d %H:%M} |" if latest else f"| {name} | none |")
    return "\n".join(lines) + "\n", end


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="use synthetic rows from data/sample")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    args = parser.parse_args()
    as_of = args.as_of or (settings["sample_as_of"] if args.dry_run else date.today())

    con = connect(warehouse_path(args.dry_run))
    loaded = ingest_all(con, data_dir(args.dry_run))
    build(con)
    text, week_end = render(con, as_of, settings, args.dry_run)

    REPORTS_DIR.mkdir(exist_ok=True)
    prefix = "sample_" if args.dry_run else ""
    out = REPORTS_DIR / f"{prefix}weekly_{week_end}_{datetime.now():%Y%m%d-%H%M%S}.md"
    out.write_text(text, encoding="utf-8")
    new_rows = sum(n for per_source in loaded.values() for n in per_source.values())
    print(text)
    print(f"new raw rows this run: {new_rows}")
    print(f"written: {out}")


if __name__ == "__main__":
    main()
