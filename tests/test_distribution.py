"""The public distributions contain only the standalone shell."""

from __future__ import annotations

import re
import tarfile
import zipfile
from pathlib import Path


DIST = Path(__file__).resolve().parents[1] / "dist"


def _wheel_members() -> list[str]:
    wheel, = DIST.glob("nuluca_modeling-*.whl")
    with zipfile.ZipFile(wheel) as archive:
        return [item.filename for item in archive.infolist() if not item.is_dir()]


def _sdist_members() -> list[str]:
    source, = DIST.glob("nuluca_modeling-*.tar.gz")
    with tarfile.open(source) as archive:
        return [item.name for item in archive.getmembers() if item.isfile()]


def test_wheel_contains_only_shell_package_and_standard_metadata():
    allowed = re.compile(r"^(nuluca_modeling_connect(?:/[^/]+)?|nuluca_modeling-[^/]+\.dist-info/[^/]+)$")
    members = _wheel_members()
    assert members
    assert all(allowed.fullmatch(item) for item in members), members


def test_sdist_contains_only_shell_source_and_package_metadata():
    members = _sdist_members()
    assert members
    root, = {item.split("/", 1)[0] for item in members}
    allowed = re.compile(
        rf"^{re.escape(root)}/(?:PKG-INFO|pyproject\.toml|README\.md|src/nuluca_modeling_connect(?:/[^/]+)?)$"
    )
    assert all(allowed.fullmatch(item) for item in members), members
