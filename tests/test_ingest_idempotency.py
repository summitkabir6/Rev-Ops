import json

from src.db import connect
from src.ingest.run import ingest_all
from src.model.build import build

RAW_TABLES = ["raw_shopify", "raw_klaviyo", "raw_posthog"]


def raw_counts(con):
    return {t: con.execute(f"SELECT count(*) FROM {t}").fetchall()[0][0] for t in RAW_TABLES}


def test_second_load_inserts_nothing(sample_dir):
    con = connect(":memory:")
    first = ingest_all(con, sample_dir)
    counts_after_first = raw_counts(con)
    second = ingest_all(con, sample_dir)

    assert sum(n for src in first.values() for n in src.values()) > 0
    assert all(n == 0 for src in second.values() for n in src.values())
    assert raw_counts(con) == counts_after_first


def test_every_raw_row_carries_source_load_time_and_hash(sample_dir):
    con = connect(":memory:")
    ingest_all(con, sample_dir)
    for table in RAW_TABLES:
        missing = con.execute(f"""
            SELECT count(*) FROM {table}
            WHERE source_name IS NULL OR loaded_at IS NULL OR length(row_hash) <> 64
        """).fetchall()[0][0]
        assert missing == 0, table


def test_changed_row_is_appended_and_latest_version_wins(sample_dir):
    con = connect(":memory:")
    ingest_all(con, sample_dir)
    before = raw_counts(con)["raw_shopify"]

    # Shopify re-sends order #5000 after a partial refund.
    path = sample_dir / "shopify" / "orders_api_sample.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["displayFinancialStatus"] = "PARTIALLY_REFUNDED"
    rows[0]["totalRefundedSet"] = {"shopMoney": {"amount": "5.00"}}
    (sample_dir / "shopify" / "orders_api_repull.jsonl").write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")

    ingest_all(con, sample_dir)
    build(con)

    assert raw_counts(con)["raw_shopify"] == before + 1
    staged = con.execute(
        "SELECT financial_status, refunded_amount FROM stg_shopify_orders WHERE order_name = ? AND stream = 'orders_api'",
        [rows[0]["name"]],
    ).fetchall()
    assert len(staged) == 1
    assert staged[0][0] == "PARTIALLY_REFUNDED"
    assert float(staged[0][1]) == 5.0
