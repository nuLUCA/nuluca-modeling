"""Release workflow boundaries: who can publish, and what gets published."""

from pathlib import Path

import yaml

from tests.check_dist_contents import problems

WORKFLOW = yaml.safe_load(Path(".github/workflows/publish.yml").read_text())
JOBS = WORKFLOW["jobs"]


def test_build_job_has_no_publishing_identity():
    assert "id-token" not in (JOBS["build-test"].get("permissions") or {})


def test_publish_job_runs_only_for_tags_in_the_protected_environment():
    publish = JOBS["publish"]
    assert publish["needs"] == "build-test"
    assert "github.ref_type == 'tag'" in publish["if"]
    assert publish["environment"]["name"] == "pypi"
    assert publish["permissions"] == {"id-token": "write"}


def test_inspection_reports_are_a_separate_artifact():
    steps = JOBS["build-test"]["steps"]
    uploads = [s for s in steps if s.get("uses", "").startswith("actions/upload-artifact")]
    names = {s["with"]["name"]: s["with"]["path"] for s in uploads}
    assert set(names) == {"nuluca-modeling-distributions", "nuluca-modeling-inspection"}
    assert "inspection" not in names["nuluca-modeling-distributions"]
    assert "members.txt" not in names["nuluca-modeling-distributions"]


def test_release_tag_is_checked_against_version_and_main():
    steps = JOBS["build-test"]["steps"]
    check = next(s for s in steps if s.get("name", "").startswith("Release tags must"))
    assert check["if"] == "github.ref_type == 'tag'"
    assert "merge-base --is-ancestor" in check["run"]
    assert 'v${version}' in check["run"]
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout"))
    assert checkout["with"]["fetch-depth"] == 0


def test_dist_check_rejects_reports_and_extra_files(tmp_path):
    (tmp_path / "nuluca_modeling-0.3.0-py3-none-any.whl").write_text("w")
    (tmp_path / "nuluca_modeling-0.3.0.tar.gz").write_text("s")
    assert problems(tmp_path) == []
    (tmp_path / "nuluca_modeling-0.3.0.tar.gz.members.txt").write_text("r")
    assert problems(tmp_path) == ["unexpected files: ['nuluca_modeling-0.3.0.tar.gz.members.txt']"]


def test_dist_check_requires_exactly_one_of_each(tmp_path):
    (tmp_path / "a.whl").write_text("w")
    (tmp_path / "b.whl").write_text("w")
    assert any("one wheel" in p for p in problems(tmp_path))
    assert any("one source archive" in p for p in problems(tmp_path))


def test_a_wheel_tag_needs_pinned_plugin_hashes():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/publish.yml").read_text()
    assert "python tests/check_plugin_pins.py" in workflow
