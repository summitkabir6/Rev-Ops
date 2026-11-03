"""Klaviyo events. Grain: one event (one profile doing one thing at one time).

Files in data/<dir>/klaviyo/ are named <stream>_<pull date>.jsonl, one event per line,
as returned by the Events API with the profile email attached:
    {"id", "metric_id", "datetime", "profile_id", "profile_email", "event_properties"}

Order events carry the Shopify order id in event_properties["$event_id"] and the
order name in event_properties["$extra"]["name"]. At pull time $extra is cut to the
handful of fields the model uses, so addresses never reach disk.
"""
import json
from pathlib import Path

from .common import append_rows

STREAMS = {
    "placed_order",
    "refunded_order",
    "cancelled_order",
    "quiz_completed",
    "quiz_store_clicked",
    "received_email_quiz_flow",
    "subscribed_email",
    "unsubscribed_email",
}


def stream_from_filename(path: Path) -> str:
    stream = path.stem.rsplit("_", 1)[0]
    if stream not in STREAMS:
        raise ValueError(f"unknown Klaviyo stream in file name: {path.name}")
    return stream


def load(con, folder: Path) -> dict[str, int]:
    inserted = {}
    for path in sorted((folder / "klaviyo").glob("*.jsonl")):
        with open(path, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        inserted[path.name] = append_rows(con, "raw_klaviyo", "klaviyo", stream_from_filename(path), path.name, rows)
    return inserted
