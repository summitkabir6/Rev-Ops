import shutil
from datetime import date

import pytest

from src.config import SAMPLE_DIR
from src.db import connect
from src.ingest.run import ingest_all
from src.model.build import build

# The sample covers 29 Sep - 2 Nov 2026.
WINDOW = (date(2026, 9, 29), date(2026, 11, 2))


@pytest.fixture
def sample_dir(tmp_path):
    """A private copy of data/sample that a test may edit."""
    target = tmp_path / "sample"
    shutil.copytree(SAMPLE_DIR, target, ignore=shutil.ignore_patterns("*.py"))
    return target


@pytest.fixture
def warehouse(sample_dir):
    con = connect(":memory:")
    ingest_all(con, sample_dir)
    build(con)
    return con
