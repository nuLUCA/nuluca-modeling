"""The one-command setup keeps its key private and is safe to repeat."""

import hashlib
import json
import zipfile

import httpx
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
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "home" / ".codex"))
    monkeypatch.delenv("NULUCA_AGENT_CONNECT_URL", raising=False)


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
    monkeypatch.setattr(installer, "_member_release", lambda key, **kwargs: ("https://storage.example.test/plugin.zip", digest))
    monkeypatch.setattr(installer, "_read_source", lambda source: calls.append(source) or archive.read_bytes())

    installer.setup(installer.SetupOptions(None, None, "ae_test_secret", ("codex",)))
    assert calls == ["https://storage.example.test/plugin.zip"]

    monkeypatch.setattr(installer, "_member_release", lambda key, **kwargs: ("https://storage.example.test/plugin.zip", "0" * 64))
    with pytest.raises(installer.SetupError, match="checksum"):
        installer.setup(installer.SetupOptions(None, None, "ae_test_secret", ("codex",)))


@pytest.mark.parametrize("override, expected", [
    (None, "https://nuluca-backend-production.up.railway.app"),
    ("https://nuluca-backend-staging.up.railway.app/", "https://nuluca-backend-staging.up.railway.app"),
])
def test_download_and_both_clients_use_the_same_selected_service(tmp_path, monkeypatch, override, expected):
    home(monkeypatch, tmp_path)
    if override is not None:
        monkeypatch.setenv("NULUCA_AGENT_CONNECT_URL", override)
    archive = release(tmp_path)
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={
            "archive_url": "https://storage.example.test/plugin.zip",
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        })

    client_type = httpx.Client
    monkeypatch.setattr(installer.httpx, "Client", lambda **kwargs: client_type(
        transport=httpx.MockTransport(respond), **kwargs))
    monkeypatch.setattr(installer, "_read_source", lambda source: archive.read_bytes())
    result = installer.setup(installer.SetupOptions(None, None, "ae_test_secret", ("claude", "codex")))

    assert len(calls) == 1
    assert str(calls[0].url) == f"{expected}/v1/agent-connect/nuluca/member-downloads/jsontogh/{installer._platform()}"
    assert calls[0].headers["X-API-Key"] == "ae_test_secret"
    claude = json.loads((tmp_path / "home" / ".claude.json").read_text())
    assert claude["mcpServers"]["nuluca-modeling"]["env"]["NULUCA_AGENT_CONNECT_URL"] == expected
    codex = (tmp_path / "home" / ".codex" / "config.toml").read_text()
    assert f'NULUCA_AGENT_CONNECT_URL = "{expected}"' in codex
    assert "ae_test_secret" not in json.dumps(result)


@pytest.mark.parametrize("url", [
    "http://example.test", "https://example.test/path", "https://example.test?key=hidden",
    "https://example.test#fragment", "https://user:password@example.test", "https://[invalid",
])
def test_setup_rejects_unsafe_service_before_download_or_config_changes(tmp_path, monkeypatch, url):
    home(monkeypatch, tmp_path)
    monkeypatch.setenv("NULUCA_AGENT_CONNECT_URL", url)
    monkeypatch.setattr(installer, "_member_release", lambda *args, **kwargs: pytest.fail("download attempted"))
    with pytest.raises(installer.SetupError, match="setup service"):
        installer.setup(installer.SetupOptions(None, None, "ae_test_secret", ("claude", "codex")))
    assert not (tmp_path / "home").exists()
