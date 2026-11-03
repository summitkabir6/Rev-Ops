"""Paths and settings shared by every stage."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
SAMPLE_DIR = ROOT / "data" / "sample"
WAREHOUSE_DIR = ROOT / "warehouse"
REPORTS_DIR = ROOT / "reports"
MODEL_DIR = ROOT / "src" / "model"


def load_settings() -> dict:
    with open(ROOT / "config" / "settings.yml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def data_dir(dry_run: bool) -> Path:
    return SAMPLE_DIR if dry_run else RAW_DIR


def warehouse_path(dry_run: bool) -> Path:
    return WAREHOUSE_DIR / ("sample.duckdb" if dry_run else "rev_ops.duckdb")
