"""PostHog quiz export. Grain: one person (a browser PostHog has seen on the quiz).

Files in data/<dir>/posthog/ are pipe-delimited HogQL results, one row per person:
    person_id | person_email | first_event_at | host | app | utm_source | utm_campaign |
    utm_content | any_test | started_at | email_submitted_at | completed_at |
    store_clicked_at | steps_seen

steps_seen packs every quiz screen the person viewed as "NN:SNN" pairs joined by ';'.

Two PostHog quirks are undone here so the raw rows say what they mean:
    "(null)" is how an empty value prints; it becomes None.
    minIf() returns 1970-01-01 when the event never happened; that becomes None too.
"""
from pathlib import Path

from .common import append_rows

NEVER = "1970-01-01T00:00:00Z"


def clean(value: str):
    if value in ("(null)", "", NEVER):
        return None
    return value


def read_psv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        lines = [line.rstrip("\n") for line in fh if line.strip()]
    header = lines[0].split("|")
    rows = []
    for line in lines[1:]:
        values = line.split("|")
        if len(values) != len(header):
            raise ValueError(f"{path.name}: expected {len(header)} fields, got {len(values)}")
        rows.append({col: clean(val) for col, val in zip(header, values)})
    return rows


def load(con, folder: Path) -> dict[str, int]:
    inserted = {}
    for path in sorted((folder / "posthog").glob("*.psv")):
        inserted[path.name] = append_rows(con, "raw_posthog", "posthog", "quiz_people", path.name, read_psv(path))
    return inserted
