"""Auto-generate EXR fixtures if tests/data/ is missing them."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_DATA_DIR = Path(__file__).parents[1] / "data"
_MARKER = _DATA_DIR / "infinity_attributes.exr"


@pytest.fixture(scope="session", autouse=True)
def ensure_fixtures() -> None:
    if _MARKER.exists():
        return

    result = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "generate_fixtures.py")],
        check=False,
    )
    if result.returncode != 0:
        pytest.fail("EXR fixtures are missing and generate_fixtures.py failed. Run: make test/fixtures")
