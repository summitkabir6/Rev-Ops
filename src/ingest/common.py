"""Append-only raw storage shared by all three loaders.

Every source lands in its own raw table with the same columns:
    source_name  which system the row came from
    stream       which file shape or event stream (e.g. orders_export_v2, placed_order)
    source_file  the file name it was read from
    loaded_at    when this run inserted it (UTC)
    row_hash     sha256 of the source row, so re-running a load inserts nothing new
    payload      the source row, untouched, as JSON

Rows are never updated or deleted. If a source re-sends a row with a changed value,
its hash differs and it is appended; the staging SQL keeps the latest version.
"""
import hashlib
import json
from datetime import datetime, timezone


def create_raw_table(con, table: str) -> None:
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {table} (
            source_name VARCHAR,
            stream VARCHAR,
            source_file VARCHAR,
            loaded_at TIMESTAMP,
            row_hash VARCHAR PRIMARY KEY,
            payload JSON
        )
    """)


def row_hash(row: dict) -> str:
    canonical = json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def append_rows(con, table: str, source_name: str, stream: str, source_file: str, rows: list[dict]) -> int:
    """Insert rows whose hash is not already stored. Returns how many were new."""
    create_raw_table(con, table)
    loaded_at = datetime.now(timezone.utc).replace(tzinfo=None)
    seen = {h for (h,) in con.execute(f"SELECT row_hash FROM {table}").fetchall()}
    new_rows = []
    for row in rows:
        h = row_hash(row)
        if h in seen:
            continue
        seen.add(h)
        new_rows.append((source_name, stream, source_file, loaded_at, h, json.dumps(row, ensure_ascii=False)))
    if new_rows:
        con.executemany(f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?, ?)", new_rows)
    return len(new_rows)
