"""The one-command setup keeps its key private and is safe to repeat."""

import hashlib
import json
import zipfile

import httpx
import pytest
from nuluca_modeling_connect import setup as installer


def release(tmp_path):
    archive = tmp_path / "JsonToGH-0.1.0-mac.zip"
    plugin = {"nuLUCA-for-Grasshopper.gha": b"plugin", "Newtonsoft.Json.dll": b"dependency"}
    with zipfile.ZipFile(archive, "w") as packed:
        for name, content in plugin.items():
            packed.writestr(name, content)
        packed.writestr("INSTALL.md", "restart")
        packed.writestr("LICENSE-DRAFT.txt", "pilot")
        packed.writestr("SHA256SUMS", "".join(
            f"{hashlib.sha256(content).hexdigest()}  {name}\n" for name, content in plugin.items()))
    (tmp_path / "SHA256SUMS").write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")
    return archive


def home(monkeypatch, tmp_path, *, tools=("uvx",)):
    monkeypatch.setattr(installer, "_home", lambda: tmp_path / "home")
    monkeypatch.setattr(installer, "_libraries_dir", lambda: tmp_path / "libraries")
    monkeypatch.setattr(installer, "version", lambda name: "9.8.7")
    monkeypatch.setattr(installer.shutil, "which", lambda name: f"/opt/bin/{name}" if name in tools else None)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "home" / ".codex"))
    monkeypatch.delenv("NULUCA_AGENT_CONNECT_URL", raising=False)


def test_setup_upgrades_clients_without_printing_key_and_uninstall_removes_owned_files(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    archive = release(tmp_path)
    options = installer.SetupOptions(str(archive), None, "ae_test_secret", ("claude", "codex"))
    result = installer.setup(options)

    assert "restart Rhino themselves" in result["restart"]
    assert "Never quit, kill or relaunch" in result["restart"]
    assert "nuluca_status" in result["restart"]
    assert (tmp_path / "libraries" / "nuLUCA-for-Grasshopper.gha").read_bytes() == b"plugin"
    claude = json.loads((tmp_path / "home" / ".claude.json").read_text())
    assert claude["mcpServers"]["nuluca-modeling"]["env"]["NULUCA_AGENT_CONNECT_KEY"] == "ae_test_secret"
    codex = (tmp_path / "home" / ".codex" / "config.toml").read_text()
    assert codex.count("[mcp_servers.nuluca-modeling]") == 1

    installer.setup(options)
    assert (tmp_path / "home" / ".codex" / "config.toml").read_text().count("[mcp_servers.nuluca-modeling]") == 1
    removed = installer.uninstall()["removed"]
    assert str(tmp_path / "libraries" / "nuLUCA-for-Grasshopper.gha") in removed
    assert not (tmp_path / "libraries" / "nuLUCA-for-Grasshopper.gha").exists()
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
    original = tmp_path / "libraries" / "nuLUCA-for-Grasshopper.gha"
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
    monkeypatch.setitem(installer.PINNED_ARCHIVES, installer._platform(), digest)
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
    monkeypatch.setitem(installer.PINNED_ARCHIVES, installer._platform(), hashlib.sha256(archive.read_bytes()).hexdigest())
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


def test_mac_install_goes_to_the_folder_grasshopper_loads(monkeypatch, tmp_path):
    monkeypatch.setattr(installer, "_home", lambda: tmp_path)
    monkeypatch.setattr(installer.os, "name", "posix")
    assert installer._libraries_dir() == (
        tmp_path / "Library/Application Support/McNeel/Rhinoceros/8.0/Plug-ins"
        / "Grasshopper (b45a29b1-4343-4035-989e-044e8580d9cf)/Libraries")


def old_layout(tmp_path):
    archive = tmp_path / "JsonToGH-0.1.4-mac.zip"
    plugin = {"JsonToGH_Fresh.gha": b"old-plugin", "Newtonsoft.Json.dll": b"dependency"}
    with zipfile.ZipFile(archive, "w") as packed:
        for name, content in plugin.items():
            packed.writestr(name, content)
        packed.writestr("SHA256SUMS", "".join(
            f"{hashlib.sha256(content).hexdigest()}  {name}\n" for name, content in plugin.items()))
    (tmp_path / "SHA256SUMS").write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n")
    return archive


def test_upgrade_removes_the_old_plugin_name_so_two_copies_never_load(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    installer.setup(installer.SetupOptions(str(old_layout(tmp_path)), None, "ae_test_secret", ("codex",)))
    assert (tmp_path / "libraries" / "JsonToGH_Fresh.gha").read_bytes() == b"old-plugin"

    result = installer.setup(installer.SetupOptions(str(release(tmp_path)), None, "ae_test_secret", ("codex",)))

    assert {path.name for path in (tmp_path / "libraries").iterdir()} == {
        "Newtonsoft.Json.dll", "nuLUCA-for-Grasshopper.gha"}
    assert any("JsonToGH_Fresh.gha" in note for note in result["notes"])
    assert not (tmp_path / "home/.nuluca-modeling-connect/plugin-backups/JsonToGH_Fresh.gha").exists()


def test_a_manually_installed_old_plugin_is_retired_and_restored_on_uninstall(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    old = tmp_path / "libraries" / "JsonToGH_Fresh.gha"
    old.parent.mkdir()
    old.write_bytes(b"manual-copy")

    installer.setup(installer.SetupOptions(str(release(tmp_path)), None, "ae_test_secret", ("codex",)))
    assert not old.exists()
    installer.uninstall()
    assert old.read_bytes() == b"manual-copy"
    assert not (tmp_path / "libraries" / "nuLUCA-for-Grasshopper.gha").exists()


def test_dry_run_reports_the_old_plugin_and_leaves_it(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    old = tmp_path / "libraries" / "JsonToGH_Fresh.gha"
    old.parent.mkdir()
    old.write_bytes(b"manual-copy")
    result = installer.setup(installer.SetupOptions(str(release(tmp_path)), None, "ae_test_secret", ("codex",),
                                                    dry_run=True))
    assert old.read_bytes() == b"manual-copy"
    assert any("JsonToGH_Fresh.gha" in note for note in result["notes"])


def test_a_locked_old_plugin_stops_setup_before_a_second_copy_lands(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    old = tmp_path / "libraries" / "JsonToGH_Fresh.gha"
    old.parent.mkdir()
    old.write_bytes(b"loaded")
    path_type = type(old)
    real_unlink = path_type.unlink

    def locked(self, *args, **kwargs):
        if self.name == "JsonToGH_Fresh.gha":
            raise PermissionError(13, "in use")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(path_type, "unlink", locked)
    with pytest.raises(installer.SetupError, match="close Rhino"):
        installer.setup(installer.SetupOptions(str(release(tmp_path)), None, "ae_test_secret", ("codex",)))
    assert not (tmp_path / "libraries" / "nuLUCA-for-Grasshopper.gha").exists()


def test_archive_with_both_plugin_names_is_refused(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    archive = tmp_path / "both.zip"
    with zipfile.ZipFile(archive, "w") as packed:
        for name in ("nuLUCA-for-Grasshopper.gha", "JsonToGH_Fresh.gha", "Newtonsoft.Json.dll"):
            packed.writestr(name, b"x")
        packed.writestr("SHA256SUMS", "")
    with pytest.raises(installer.SetupError, match="layout"):
        installer._safe_archive(archive.read_bytes(), hashlib.sha256(archive.read_bytes()).hexdigest())


def test_setup_moves_a_plugin_left_in_the_old_mac_folder(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    stale = tmp_path / "home/Library/Application Support/Grasshopper/Libraries/JsonToGH_Fresh.gha"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    state = tmp_path / "home/.nuluca-modeling-connect/setup-state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"plugin_paths": {"nuLUCA-for-Grasshopper.gha": str(stale)}, "plugin_restore": {}, "clients": []}))
    monkeypatch.setattr(installer.os, "name", "posix")

    installer.setup(installer.SetupOptions(str(release(tmp_path)), None, "ae_test_secret", ("codex",)))

    assert not stale.exists()
    assert (tmp_path / "libraries" / "nuLUCA-for-Grasshopper.gha").read_bytes() == b"plugin"


def test_saved_client_entry_survives_the_uvx_process_that_ran_setup(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    installer.setup(installer.SetupOptions(str(release(tmp_path)), None, "ae_test_secret", ("claude", "codex")))

    entry = json.loads((tmp_path / "home" / ".claude.json").read_text())["mcpServers"]["nuluca-modeling"]
    assert entry["command"] == "/opt/bin/uvx"
    assert entry["args"] == ["nuluca-modeling@9.8.7", "serve"]
    codex = (tmp_path / "home" / ".codex" / "config.toml").read_text()
    assert 'command = "/opt/bin/uvx"' in codex
    assert 'args = ["nuluca-modeling@9.8.7", "serve"]' in codex


def test_client_entry_can_name_a_package_before_it_is_on_pypi(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    installer.setup(installer.SetupOptions(str(release(tmp_path)), None, "ae_test_secret", ("claude",),
                                           package_spec="/tmp/nuluca_modeling.whl"))
    entry = json.loads((tmp_path / "home" / ".claude.json").read_text())["mcpServers"]["nuluca-modeling"]
    assert entry["args"] == ["--from", "/tmp/nuluca_modeling.whl", "nuluca-modeling", "serve"]


def test_installed_command_is_used_without_uvx_and_absence_is_explained(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path, tools=("nuluca-modeling",))
    archive = release(tmp_path)
    installer.setup(installer.SetupOptions(str(archive), None, "ae_test_secret", ("claude",)))
    entry = json.loads((tmp_path / "home" / ".claude.json").read_text())["mcpServers"]["nuluca-modeling"]
    assert (entry["command"], entry["args"]) == ("/opt/bin/nuluca-modeling", ["serve"])

    home(monkeypatch, tmp_path, tools=())
    with pytest.raises(installer.SetupError, match="Install uv"):
        installer.setup(installer.SetupOptions(str(archive), None, "ae_test_secret", ("claude",)))


def test_named_environment_wins_and_production_is_only_a_default(tmp_path, monkeypatch):
    monkeypatch.setenv("NULUCA_AGENT_CONNECT_URL", "https://example.test")
    assert installer._service_url("staging") == "https://nuluca-backend-staging.up.railway.app"
    assert installer._service_url("production") == "https://nuluca-backend-production.up.railway.app"
    assert installer._service_url() == "https://example.test"
    with pytest.raises(installer.SetupError):
        installer._service_url("qa")


@pytest.mark.parametrize("status, expected", [(404, "does not offer"), (403, "Modeling key"), (401, "Modeling key")])
def test_download_refusal_says_what_to_do_without_the_key(monkeypatch, status, expected):
    client_type = httpx.Client
    monkeypatch.setattr(installer.httpx, "Client", lambda **kwargs: client_type(
        transport=httpx.MockTransport(lambda request: httpx.Response(status)), **kwargs))
    with pytest.raises(installer.SetupError, match=expected) as caught:
        installer._member_release("ae_test_secret", base_url="https://example.test")
    assert "ae_test_secret" not in str(caught.value)


def test_shared_newtonsoft_is_left_alone_on_install_rerun_and_uninstall(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    archive = release(tmp_path)
    shared = tmp_path / "libraries" / "Newtonsoft.Json.dll"
    shared.parent.mkdir()
    shared.write_bytes(b"another-plugin-copy")
    options = installer.SetupOptions(str(archive), None, "ae_test_secret", ("codex",))

    first = installer.setup(options)
    second = installer.setup(options)

    assert shared.read_bytes() == b"another-plugin-copy"
    assert "Newtonsoft.Json.dll" not in first["plugin"]
    assert any("Newtonsoft.Json.dll" in note for note in second["notes"])
    assert not (tmp_path / "home/.nuluca-modeling-connect/plugin-backups/Newtonsoft.Json.dll").exists()
    installer.uninstall()
    assert shared.read_bytes() == b"another-plugin-copy"


def test_newtonsoft_setup_added_is_removed_with_the_plugin(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    installer.setup(installer.SetupOptions(str(release(tmp_path)), None, "ae_test_secret", ("codex",)))
    assert (tmp_path / "libraries" / "Newtonsoft.Json.dll").read_bytes() == b"dependency"
    installer.uninstall()
    assert not (tmp_path / "libraries" / "Newtonsoft.Json.dll").exists()


def test_rerun_rewrites_nothing_in_libraries(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    options = installer.SetupOptions(str(release(tmp_path)), None, "ae_test_secret", ("codex",))
    installer.setup(options)
    monkeypatch.setattr(installer, "_replace_file", lambda *args: pytest.fail("rewrote an unchanged file"))
    installer.setup(options)


def test_setup_does_not_change_permissions_on_the_shared_libraries_folder(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    libraries = tmp_path / "libraries"
    libraries.mkdir()
    libraries.chmod(0o755)
    installer.setup(installer.SetupOptions(str(release(tmp_path)), None, "ae_test_secret", ("codex",)))
    assert libraries.stat().st_mode & 0o777 == 0o755


def test_upgrade_while_rhino_holds_the_plugin_gives_a_readable_error(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    archive = release(tmp_path)
    installer.setup(installer.SetupOptions(str(archive), None, "ae_test_secret", ("codex",)))
    library = tmp_path / "libraries" / "nuLUCA-for-Grasshopper.gha"
    library.write_bytes(b"older-loaded-plugin")

    def locked(source, destination):
        raise PermissionError(13, "The process cannot access the file", str(destination))

    monkeypatch.setattr(installer.os, "replace", locked)
    with pytest.raises(installer.SetupError, match="close Rhino") as caught:
        installer.setup(installer.SetupOptions(str(archive), None, "ae_test_secret", ("codex",)))
    assert "13" not in str(caught.value)
    assert library.read_bytes() == b"older-loaded-plugin"
    assert {path.name for path in library.parent.iterdir()} == {"nuLUCA-for-Grasshopper.gha", "Newtonsoft.Json.dll"}


def test_other_client_changes_no_client_file_and_gets_a_keyless_entry(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    result = installer.setup(installer.SetupOptions(str(release(tmp_path)), None, "ae_test_secret", ("other",)))

    assert result["clients"] == {}
    assert result["manual_client_entry"]["command"] == "/opt/bin/uvx"
    assert result["manual_client_entry"]["args"] == ["nuluca-modeling@9.8.7", "serve"]
    assert "ae_test_secret" not in json.dumps(result)
    assert not (tmp_path / "home" / ".claude.json").exists()
    assert not (tmp_path / "home" / ".codex" / "config.toml").exists()
    installer.uninstall()


def test_default_clients_follow_what_is_installed(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path, tools=("codex",))
    assert installer.detected_clients() == ("codex",)
    home(monkeypatch, tmp_path, tools=())
    assert installer.detected_clients() == ("other",)
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / ".claude.json").write_text("{}")
    assert installer.detected_clients() == ("claude",)


def test_archive_may_carry_third_party_notices(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    archive = tmp_path / "with-notices.zip"
    plugin = {"nuLUCA-for-Grasshopper.gha": b"plugin", "Newtonsoft.Json.dll": b"dependency"}
    with zipfile.ZipFile(archive, "w") as packed:
        for name, content in plugin.items():
            packed.writestr(name, content)
        packed.writestr("THIRD-PARTY-NOTICES.txt", "Newtonsoft.Json MIT")
        packed.writestr("SHA256SUMS", "".join(
            f"{hashlib.sha256(content).hexdigest()}  {name}\n" for name, content in plugin.items()))
    assert set(installer._safe_archive(archive.read_bytes(), hashlib.sha256(archive.read_bytes()).hexdigest())) == set(plugin)


def download(monkeypatch, archive):
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setattr(installer, "_member_release", lambda key, **kwargs: ("https://storage.example.test/plugin.zip", digest))
    monkeypatch.setattr(installer, "_read_source", lambda source: archive.read_bytes())
    return digest


def test_a_download_the_wheel_did_not_pin_is_refused_even_with_a_matching_server_checksum(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    download(monkeypatch, release(tmp_path))
    monkeypatch.setitem(installer.PINNED_ARCHIVES, installer._platform(), "0" * 64)
    with pytest.raises(installer.SetupError, match="does not match this nuluca-modeling release"):
        installer.setup(installer.SetupOptions(None, None, "ae_test_secret", ("codex",), environment="staging"))
    assert not (tmp_path / "libraries").exists()


def test_production_needs_a_pin_and_staging_says_it_has_none(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    download(monkeypatch, release(tmp_path))
    monkeypatch.setattr(installer, "PINNED_ARCHIVES", {})
    with pytest.raises(installer.SetupError, match="no pinned plugin hash"):
        installer.setup(installer.SetupOptions(None, None, "ae_test_secret", ("codex",), environment="production"))
    result = installer.setup(installer.SetupOptions(None, None, "ae_test_secret", ("codex",), environment="staging"))
    assert any("no pinned plugin hash" in note for note in result["notes"])


def test_this_wheel_ships_no_invented_pins():
    assert installer.PINNED_ARCHIVES == {}


def codex_table(tmp_path):
    import tomllib
    return tomllib.loads((tmp_path / "home/.codex/config.toml").read_text())["mcp_servers"]["nuluca-modeling"]


def test_clients_ask_before_script_imports_by_default(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    result = installer.setup(installer.SetupOptions(str(release(tmp_path)), None, "ae_test_secret", ("claude", "codex")))

    assert codex_table(tmp_path)["tools"]["canvas_import_scripts"]["approval_mode"] == "prompt"
    assert not (tmp_path / "home/.claude/settings.json").exists()
    assert result["script_tool"]["always_allow"] is False
    assert "--always-allow-scripts" in result["script_tool"]["next_step"]


def test_always_allow_is_the_member_choice_and_lasts_across_setup_runs(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    archive = str(release(tmp_path))
    settings = tmp_path / "home/.claude/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"theme": "dark", "permissions": {"allow": ["Bash(ls)"]}}))
    clients = ("claude", "codex")

    result = installer.setup(installer.SetupOptions(archive, None, "ae_test_secret", clients, always_allow_scripts=True))
    assert json.loads(settings.read_text())["permissions"]["allow"] == ["Bash(ls)", "mcp__nuluca-modeling__canvas_import_scripts"]
    assert json.loads(settings.read_text())["theme"] == "dark"
    assert codex_table(tmp_path)["tools"]["canvas_import_scripts"]["approval_mode"] == "approve"
    assert "get their approval" in result["script_tool"]["next_step"]

    installer.setup(installer.SetupOptions(archive, None, "ae_test_secret", clients))
    assert "mcp__nuluca-modeling__canvas_import_scripts" in json.loads(settings.read_text())["permissions"]["allow"]

    installer.setup(installer.SetupOptions(archive, None, "ae_test_secret", clients, always_allow_scripts=False))
    assert json.loads(settings.read_text())["permissions"]["allow"] == ["Bash(ls)"]
    assert codex_table(tmp_path)["tools"]["canvas_import_scripts"]["approval_mode"] == "prompt"


def test_uninstall_removes_always_allow_and_every_codex_table(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    codex = tmp_path / "home/.codex/config.toml"
    codex.parent.mkdir(parents=True)
    codex.write_text('[mcp_servers.other]\ncommand = "x"\n')
    settings = tmp_path / "home/.claude/settings.json"
    installer.setup(installer.SetupOptions(str(release(tmp_path)), None, "ae_test_secret", ("claude", "codex"),
                                           always_allow_scripts=True))
    installer.uninstall()

    assert json.loads(settings.read_text())["permissions"]["allow"] == []
    assert "nuluca-modeling" not in codex.read_text()
    assert "[mcp_servers.other]" in codex.read_text()


def test_unreadable_claude_settings_stop_setup_rather_than_being_replaced(tmp_path, monkeypatch):
    home(monkeypatch, tmp_path)
    settings = tmp_path / "home/.claude/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{not json")
    with pytest.raises(installer.SetupError, match="could not be read safely"):
        installer.setup(installer.SetupOptions(str(release(tmp_path)), None, "ae_test_secret", ("claude",),
                                               always_allow_scripts=True))
    assert settings.read_text() == "{not json"
