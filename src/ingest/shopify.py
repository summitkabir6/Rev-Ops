"""Shopify orders. Grain: one order (API pull) or one line item (CSV export).

Three file shapes arrive in data/<dir>/shopify/:

  orders_api_*.jsonl      Admin GraphQL pull, one order per line, money as strings.
  orders_export_*.csv     The admin "Export orders" CSV, which changed shape:
      v1  9 columns, Windows-1252, a shipping-label export with no dates or money.
      v2  75 columns, UTF-8 with BOM, one row per line item; order totals sit on
          the first row of each order only.

The version is detected from the header, never from the file name. An unknown
header stops the load instead of guessing, because a silently mis-mapped
column is worse than a failed run.
"""
import csv
import io
import json
from pathlib import Path

from .common import append_rows

EXPORT_V1_COLUMNS = [
    "Name", "Email", "Lineitem name", "Shipping Name", "Shipping City",
    "Shipping Province", "Shipping Country", "Notes", "Shipping Province Name",
]
# v2 has 75 columns; these are the ones the model relies on.
EXPORT_V2_REQUIRED = ["Name", "Email", "Financial Status", "Created at", "Total", "Refunded Amount", "Id"]


class UnknownSchema(ValueError):
    pass


def read_text(path: Path) -> str:
    """v2 exports are UTF-8; v1 exports were saved as Windows-1252."""
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def detect_export_version(header: list[str]) -> str:
    if header == EXPORT_V1_COLUMNS:
        return "orders_export_v1"
    if all(col in header for col in EXPORT_V2_REQUIRED):
        return "orders_export_v2"
    raise UnknownSchema(f"unrecognised Shopify export header ({len(header)} columns): {header[:6]}...")


def read_export(path: Path) -> tuple[str, list[dict]]:
    reader = csv.reader(io.StringIO(read_text(path)))
    header = next(reader)
    stream = detect_export_version(header)
    rows = [dict(zip(header, values)) for values in reader if any(values)]
    return stream, rows


def read_api(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load(con, folder: Path) -> dict[str, int]:
    inserted = {}
    for path in sorted((folder / "shopify").glob("*")):
        if path.suffix == ".jsonl":
            stream, rows = "orders_api", read_api(path)
        elif path.suffix == ".csv":
            stream, rows = read_export(path)
        else:
            continue
        inserted[path.name] = append_rows(con, "raw_shopify", "shopify", stream, path.name, rows)
    return inserted
