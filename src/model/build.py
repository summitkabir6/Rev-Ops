"""Run the model SQL files in name order against the warehouse.

Settings are substituted into the SQL as plain tokens, so the files stay readable
SQL rather than templates: __TIMEZONE__, __QUIZ_HOST__, __QUIZ_APP__ and
__INTERNAL_HASHES__.
"""
import re

import yaml

from src.config import MODEL_DIR, ROOT, load_settings
from src.ingest.common import create_raw_table


def load_internal_hashes() -> list[str]:
    with open(ROOT / "config" / "internal_emails.yml", encoding="utf-8") as fh:
        hashes = yaml.safe_load(fh).get("sha256") or []
    for h in hashes:
        if not re.fullmatch(r"[0-9a-f]{64}", h):
            raise ValueError(f"config/internal_emails.yml: not a sha256 hex digest: {h!r}")
    return hashes


def build(con) -> list[str]:
    settings = load_settings()
    hashes = load_internal_hashes()
    tokens = {
        "__TIMEZONE__": settings["timezone"],
        "__QUIZ_HOST__": settings["quiz"]["host"],
        "__QUIZ_APP__": settings["quiz"]["app"],
        # An empty IN () is invalid SQL, so an empty list becomes a value no hash can equal.
        "__INTERNAL_HASHES__": ", ".join(f"'{h}'" for h in hashes) or "NULL",
    }
    # A source with no files yet still gets an empty raw table, so the model always builds.
    for table in ("raw_shopify", "raw_klaviyo", "raw_posthog"):
        create_raw_table(con, table)
    ran = []
    for path in sorted(MODEL_DIR.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        for token, value in tokens.items():
            sql = sql.replace(token, value)
        con.execute(sql)
        ran.append(path.name)
    return ran
