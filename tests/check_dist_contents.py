"""Fail unless a distributions folder holds exactly one wheel and one source archive.

Run in the release workflow before upload, so inspection reports or stray files can
never reach the PyPI publisher.
"""

from __future__ import annotations

import sys
from pathlib import Path


def problems(dist: Path) -> list[str]:
    files = sorted(p.name for p in dist.iterdir() if p.is_file())
    wheels = [f for f in files if f.endswith(".whl")]
    sdists = [f for f in files if f.endswith(".tar.gz")]
    found = []
    if len(wheels) != 1:
        found.append(f"expected one wheel, found {wheels}")
    if len(sdists) != 1:
        found.append(f"expected one source archive, found {sdists}")
    extra = [f for f in files if f not in wheels + sdists]
    if extra:
        found.append(f"unexpected files: {extra}")
    return found


if __name__ == "__main__":
    issues = problems(Path(sys.argv[1] if len(sys.argv) > 1 else "dist"))
    for issue in issues:
        print(issue, file=sys.stderr)
    sys.exit(1 if issues else 0)
