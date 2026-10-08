"""The one-command setup keeps its key private and is safe to repeat."""

import hashlib
import json
import zipfile

import pytest
from nuluca_modeling_connect import setup as installer


def release(tmp_path):
    archive = tmp_path / "JsonToGH-0.1.0-mac.zip"
    plugin = {"JsonToGH_Fresh.gha": b"plugin", "Newtonsoft.Json.dll": b"dependency"}
    with zipfile.ZipFile(archive, "w") as packed:
        for name, content in plugin.items():
            packed.writestr(name, content)
        packed.writestr("INSTALL.md", "restart")
        packed.writestr("LICENSE-DRAFT.txt", "pilot")
        packed.writestr("SHA256SUMS", "".join(
            f"{hashlib.sha256(content).hexdigest()}  {name}\n" for name, content in plugin.items()))
    (tmp_path / "SHA256SUMS").write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")
    return archive


def home(monkeypatch, tmp_path):
    monkeypatch.setattr(installer, "_home", lambda: tmp_path / "home")
    monkeypatch.setattr(installer, "_libraries_dir", lambda: tmp_path / "libraries")


def test_setup_upgrades_clients_without_printing_key_and_uninstall_removes_owned_files(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    archive = release(tmp_path)
    options = installer.SetupOptions(str(archive), None, "ae_test_secret", ("claude", "codex"))
    result = installer.setup(options)

    assert result["restart"] == "Restart Rhino, then call nuluca_status."
    assert (tmp_path / "libraries" / "JsonToGH_Fresh.gha").read_bytes() == b"plugin"
    claude = json.loads((tmp_path / "home" / ".claude.json").read_text())
    assert claude["mcpServers"]["nuluca-modeling"]["env"]["NULUCA_AGENT_CONNECT_KEY"] == "ae_test_secret"
    codex = (tmp_path / "home" / ".codex" / "config.toml").read_text()
    assert codex.count("[mcp_servers.nuluca-modeling]") == 1

    installer.setup(options)
    assert (tmp_path / "home" / ".codex" / "config.toml").read_text().count("[mcp_servers.nuluca-modeling]") == 1
    removed = installer.uninstall()["removed"]
    assert str(tmp_path / "libraries" / "JsonToGH_Fresh.gha") in removed
    assert not (tmp_path / "libraries" / "JsonToGH_Fresh.gha").exists()
    assert "nuluca-modeling" not in json.loads((tmp_path / "home" / ".claude.json").read_text())["mcpServers"]


def test_setup_refuses_a_zip_that_does_not_match_published_sums(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    archive = release(tmp_path)
    archive.write_bytes(archive.read_bytes() + b"tampered")
    with pytest.raises(installer.SetupError, match="checksum"):
        installer.setup(installer.SetupOptions(str(archive), None, "ae_test_secret", ("codex",)))


def test_uninstall_restores_a_plugin_that_setup_upgraded(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    archive = release(tmp_path)
    original = tmp_path / "libraries" / "JsonToGH_Fresh.gha"
    original.parent.mkdir()
    original.write_bytes(b"previous-plugin")

    installer.setup(installer.SetupOptions(str(archive), None, "ae_test_secret", ("codex",)))
    installer.uninstall()

    assert original.read_bytes() == b"previous-plugin"


def test_setup_requires_key_in_environment_shape(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    archive = release(tmp_path)
    with pytest.raises(installer.SetupError, match="required"):
        installer.setup(installer.SetupOptions(str(archive), None, "not-a-member-key", ("codex",)))


def test_default_setup_uses_member_download_and_refuses_a_mismatched_archive(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    archive = release(tmp_path)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    calls = []
    monkeypatch.setattr(installer, "_member_release", lambda key: ("https://storage.example.test/plugin.zip", digest))
    monkeypatch.setattr(installer, "_read_source", lambda source: calls.append(source) or archive.read_bytes())

    installer.setup(installer.SetupOptions(None, None, "ae_test_secret", ("codex",)))
    assert calls == ["https://storage.example.test/plugin.zip"]

    monkeypatch.setattr(installer, "_member_release", lambda key: ("https://storage.example.test/plugin.zip", "0" * 64))
    with pytest.raises(installer.SetupError, match="checksum"):
        installer.setup(installer.SetupOptions(None, None, "ae_test_secret", ("codex",)))
