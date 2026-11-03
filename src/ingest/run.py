"""Run all three loaders against one data folder."""
from pathlib import Path

from . import klaviyo, posthog, shopify


def ingest_all(con, folder: Path) -> dict[str, dict[str, int]]:
    return {
        "shopify": shopify.load(con, folder),
        "klaviyo": klaviyo.load(con, folder),
        "posthog": posthog.load(con, folder),
    }
