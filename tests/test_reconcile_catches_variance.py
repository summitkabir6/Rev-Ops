import json

from src.config import load_settings
from src.db import connect
from src.ingest.posthog import NEVER
from src.ingest.run import ingest_all
from src.model.build import build
from src.reconcile.reconcile import compute_metrics, explain, severity

from .conftest import WINDOW

SETTINGS = load_settings()


def row(rows, metric, source):
    return next(r for r in rows if r.metric == metric and r.source == source)


def test_an_injected_phantom_order_is_flagged_and_explained(sample_dir, warehouse):
    before = compute_metrics(warehouse, *WINDOW, SETTINGS)
    base = row(before, "order_count", "klaviyo")

    # Klaviyo receives a Placed Order event for an order Shopify never had.
    phantom = {
        "id": "KP-phantom", "metric_id": "M_PLACED", "datetime": "2026-10-15T15:00:00+00:00",
        "profile_id": "P-phantom", "profile_email": "phantom@example.com",
        "event_properties": {"$event_id": "123456789", "$value": 499.0, "$currency_code": "CAD",
                             "$extra": {"name": "#9999", "email": "phantom@example.com"}},
    }
    (sample_dir / "klaviyo" / "placed_order_injected.jsonl").write_text(json.dumps(phantom) + "\n", encoding="utf-8")
    ingest_all(warehouse, sample_dir)
    build(warehouse)

    after = compute_metrics(warehouse, *WINDOW, SETTINGS)
    flagged = row(after, "order_count", "klaviyo")
    assert flagged.variance == base.variance + 1
    assert flagged.severity in ("warn", "critical")
    assert row(after, "gross_revenue", "klaviyo").variance == round(row(before, "gross_revenue", "klaviyo").variance + 499.0, 2)

    exception = next(e for e in explain(warehouse, after, *WINDOW, SETTINGS) if e.metric == "order_count")
    assert exception.likely_cause.label.startswith("Klaviyo Placed Order events with no Shopify order")
    assert exception.likely_cause.records == 2  # the sample's test order plus the phantom
    assert exception.explained == flagged.variance


def test_a_dropped_quiz_lead_is_flagged(sample_dir, warehouse):
    before = row(compute_metrics(warehouse, *WINDOW, SETTINGS), "quiz_email_captures", "posthog").variance

    # PostHog loses one identified person who submitted an email. Raw is append-only,
    # so a deletion is simulated by loading the edited export into a fresh warehouse.
    path = sample_dir / "posthog" / "quiz_people_sample.psv"
    lines = path.read_text(encoding="utf-8").splitlines()
    victim = next(i for i, line in enumerate(lines)
                  if line.startswith("p-") and line.split("|")[1] != "(null)" and line.split("|")[10] != NEVER)
    del lines[victim]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    con = connect(":memory:")
    ingest_all(con, sample_dir)
    build(con)
    after = row(compute_metrics(con, *WINDOW, SETTINGS), "quiz_email_captures", "posthog")
    assert after.variance == before - 1
    assert after.severity in ("warn", "critical")


def test_thresholds_match_the_agreed_rules():
    t = SETTINGS["thresholds"]
    assert severity("count", 0, 0.0, t) == "ok"
    assert severity("count", 1, 0.5, t) == "warn"      # any count difference flags
    assert severity("count", 3, 6.0, t) == "critical"  # above 5%
    assert severity("money", 10.0, 0.9, t) == "ok"     # money within 1%
    assert severity("money", 10.0, 1.5, t) == "warn"
    assert severity("money", 10.0, 5.1, t) == "critical"


def test_matching_sources_are_not_flagged(warehouse):
    rows = compute_metrics(warehouse, *WINDOW, SETTINGS)
    truth_rows = [r for r in rows if r.is_source_of_truth]
    assert truth_rows and all(r.severity == "ok" for r in truth_rows)
    # The sample's only gross revenue gap is the test order, under the 1% money limit.
    assert row(rows, "gross_revenue", "klaviyo").severity == "ok"
