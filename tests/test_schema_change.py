import pytest

from src.ingest.shopify import UnknownSchema, read_export


def test_v1_export_is_detected_and_decoded_as_windows_1252(sample_dir):
    stream, rows = read_export(sample_dir / "shopify" / "orders_export_v1_sample.csv")
    assert stream == "orders_export_v1"
    assert len(rows) == 4
    assert rows[0]["Notes"].endswith("’")  # a curly apostrophe survives the cp1252 decode


def test_v2_export_is_detected_and_line_items_collapse_to_orders(sample_dir, warehouse):
    stream, rows = read_export(sample_dir / "shopify" / "orders_export_v2_sample.csv")
    assert stream == "orders_export_v2"
    assert len(rows) == 6  # 3 orders x 2 line items
    orders = warehouse.execute(
        "SELECT count(*), count(gross_amount) FROM stg_shopify_orders WHERE stream = 'orders_export_v2'"
    ).fetchall()[0]
    assert orders == (3, 3)


def test_v1_orders_reach_the_model_without_money(warehouse):
    has_money = warehouse.execute(
        "SELECT DISTINCT has_money FROM fct_order WHERE source_of_truth = 'orders_export_v1'"
    ).fetchall()
    assert has_money == [(False,)]


def test_unknown_header_stops_the_load(tmp_path):
    path = tmp_path / "orders_export_new.csv"
    path.write_text("Order,Email Address,Amount\n#1,a@example.com,10\n", encoding="utf-8")
    with pytest.raises(UnknownSchema):
        read_export(path)
