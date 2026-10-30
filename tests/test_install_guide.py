"""The one install guide agrees with what setup does."""

from pathlib import Path

from nuluca_modeling_connect import setup as installer

ROOT = Path(__file__).resolve().parents[1]
GUIDE = (ROOT / "INSTALL.md").read_text()
README = (ROOT / "README.md").read_text()


def test_guide_names_the_folders_setup_writes_to(monkeypatch, tmp_path):
    monkeypatch.setattr(installer, "_home", lambda: tmp_path)
    monkeypatch.setattr(installer.os, "name", "posix")
    mac = installer._libraries_dir().relative_to(tmp_path).as_posix()
    assert f"~/{mac}" in GUIDE
    assert "%APPDATA%\\Grasshopper\\Libraries" in GUIDE


def test_guide_covers_unblocking_on_both_systems():
    assert "xattr -d com.apple.quarantine" in GUIDE
    assert "Unblock-File" in GUIDE


def test_only_the_member_restarts_rhino():
    for text in (GUIDE, README):
        assert "Never quit, kill or relaunch the member's Rhino." in text
    for phrase in ("graceful", "osascript", "Stop-Process", "pkill", "taskkill"):
        assert phrase not in GUIDE and phrase not in README


def test_guide_says_definitions_run_code_outside_the_sandbox():
    assert "Definitions can run code, as any Grasshopper file with script components can." in GUIDE
    assert "outside" in GUIDE and "`allow_scripts`" in GUIDE
    assert "allow-agent-code-execution" in GUIDE
    assert "does not expose Python" not in README


def test_guide_uses_the_current_plugin_file_and_retires_the_old_one():
    assert installer.PLUGIN_BINARIES[0] in GUIDE
    assert f"Delete `{installer.PLUGIN_BINARIES[1]}`" in GUIDE


def test_guide_confirms_with_nuluca_status_and_serves_any_client():
    assert "`nuluca_status`" in GUIDE
    assert "--client other" in GUIDE


def test_every_documented_bridge_message_exists_in_the_code():
    bridge = (ROOT / "src/nuluca_modeling_connect/bridge.py").read_text()
    for message in ("Start Rhino and open Grasshopper first.", "No running Rhino bridge is available.",
                    "Select one running Rhino bridge"):
        assert message in bridge and message in GUIDE
