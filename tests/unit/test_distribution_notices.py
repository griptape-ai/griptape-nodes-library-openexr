"""Source and built distributions retain colour-source notices and licence text."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NOTICE_PATHS = ("LICENSE", "THIRD_PARTY_NOTICES.md", "licenses/ACES-DOCUMENTATION-CC-BY-4.0.txt")


@pytest.fixture(scope="module")
def distributions(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # Given an isolated source tree containing the package and distribution metadata.
    source = tmp_path_factory.mktemp("notice-source")
    for name in ("pyproject.toml", "README.md", *NOTICE_PATHS):
        original = ROOT / name
        if original.exists():
            destination = source / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, destination)
    shutil.copytree(
        ROOT / "griptape_nodes_openexr", source / "griptape_nodes_openexr", ignore=shutil.ignore_patterns("__pycache__")
    )
    output = tmp_path_factory.mktemp("notice-distributions")

    # When building both distributions. The normal uv build also builds the wheel from the sdist.
    result = subprocess.run(
        ["uv", "build", "--offline", "--out-dir", str(output)],
        cwd=source,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return output


def test_source_notices_identify_the_documentation_and_local_derivation() -> None:
    notices = ROOT / "THIRD_PARTY_NOTICES.md"
    assert notices.is_file()
    text = notices.read_text(encoding="utf-8")
    for required in (
        "Contributors to the ACES Documentation",
        "CC-BY-4.0",
        "ef03499b58a349de4d09c4c739742dab4ca61122",
        "Bradford",
        "ITU-R BT.709-6",
        "International Color Consortium",
    ):
        assert required in text
    assert "THIRD_PARTY_NOTICES.md" in (ROOT / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", NOTICE_PATHS)
def test_sdist_preserves_notices_verbatim(distributions: Path, name: str) -> None:
    # Then source recipients receive the original notice bytes.
    with tarfile.open(next(distributions.glob("*.tar.gz"))) as archive:
        member = next((item for item in archive.getmembers() if item.name.endswith("/" + name)), None)
        assert member is not None, name
        stream = archive.extractfile(member)
        assert stream is not None
        with stream:
            assert stream.read() == (ROOT / name).read_bytes()


@pytest.mark.parametrize("name", NOTICE_PATHS)
def test_wheel_preserves_notices_verbatim(distributions: Path, name: str) -> None:
    # Then installed-wheel recipients receive notices in the distribution's licence directory.
    with zipfile.ZipFile(next(distributions.glob("*.whl"))) as archive:
        member = next((item for item in archive.namelist() if item.endswith(".dist-info/licenses/" + name)), None)
        assert member is not None, name
        assert archive.read(member) == (ROOT / name).read_bytes()


def test_documentation_licence_matches_pinned_upstream_bytes() -> None:
    path = ROOT / "licenses/ACES-DOCUMENTATION-CC-BY-4.0.txt"
    assert path.is_file()
    assert (
        hashlib.sha256(path.read_bytes()).hexdigest()
        == "95df2e9564862e51d69683a899b6dcc8218d577057bdf67322880769ff85f29e"
    )
