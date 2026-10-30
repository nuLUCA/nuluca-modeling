"""Install the member shell's local dependencies without exposing its key."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

import httpx

PLUGIN_NAME = "nuluca-modeling"
PLUGIN_FILES = ("JsonToGH_Fresh.gha", "Newtonsoft.Json.dll")
# Other Grasshopper plugins load this same file from Libraries, so setup adds it
# only when the folder has none and never replaces or removes one it did not add.
SHARED_FILES = ("Newtonsoft.Json.dll",)
ARCHIVE_DOCUMENTS = ("INSTALL.md", "LICENSE-DRAFT.txt", "THIRD-PARTY-NOTICES.txt", "SHA256SUMS")
SERVICES = {
    "production": "https://nuluca-backend-production.up.railway.app",
    "staging": "https://nuluca-backend-staging.up.railway.app",
}
DEFAULT_URL = SERVICES["production"]
CONFIGURED_CLIENTS = ("claude", "codex")
# Any other MCP client: setup changes no client file and returns the stdio entry.
OTHER_CLIENT = "other"
RESTART_STEP = ("Ask the member to save their work and restart Rhino themselves. Never quit, kill or "
                "relaunch their Rhino. After they restart it, call nuluca_status.")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SetupError(Exception):
    """A setup failure that does not expose credentials or host internals."""


@dataclass(frozen=True)
class SetupOptions:
    source: str | None
    checksums: str | None
    key: str
    clients: tuple[str, ...]
    dry_run: bool = False
    environment: str | None = None
    package_spec: str | None = None


def _home() -> Path:
    return Path.home()


def _state_dir() -> Path:
    return _home() / ".nuluca-modeling-connect"


def _state_file() -> Path:
    return _state_dir() / "setup-state.json"


def _backup_dir() -> Path:
    return _state_dir() / "plugin-backups"


def _libraries_dir() -> Path:
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise SetupError("Windows application data is unavailable.")
        return Path(appdata) / "Grasshopper" / "Libraries"
    return (_home() / "Library" / "Application Support" / "McNeel" / "Rhinoceros" / "8.0" / "Plug-ins"
            / "Grasshopper (b45a29b1-4343-4035-989e-044e8580d9cf)" / "Libraries")


def _stale_libraries_dirs() -> tuple[Path, ...]:
    """Folders earlier setups wrote to. Grasshopper never loaded from them."""
    if os.name == "nt":
        return ()
    return (_home() / "Library" / "Application Support" / "Grasshopper" / "Libraries",)


def _claude_config() -> Path:
    return _home() / ".claude.json"


def _codex_config() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(_home() / ".codex"))) / "config.toml"


def _secure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)


def _atomic_write(path: Path, content: str, *, mode: int = 0o600) -> None:
    _secure_dir(path.parent)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=f".{path.name}.", delete=False) as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    if os.name != "nt":
        temporary.chmod(mode)
    os.replace(temporary, path)


def _read_source(source: str) -> bytes:
    parsed = urlsplit(source)
    if parsed.scheme == "https" and parsed.netloc and not parsed.username and not parsed.password:
        try:
            with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
                response = client.get(source)
            if response.is_redirect or response.status_code != 200:
                raise SetupError("The plugin download was unavailable.")
            return response.content
        except httpx.HTTPError as exc:
            raise SetupError("The plugin download was unavailable.") from exc
    if parsed.scheme:
        raise SetupError("The plugin source must be a local file or HTTPS URL.")
    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise SetupError("The plugin archive was not found.")
    return path.read_bytes()


def _service_url(environment: str | None = None) -> str:
    if environment is not None and environment not in SERVICES:
        raise SetupError("The nuLUCA setup service is unavailable.")
    value = (SERVICES[environment] if environment else
             os.environ.get("NULUCA_AGENT_CONNECT_URL") or DEFAULT_URL).rstrip("/")
    try:
        origin = urlsplit(value)
    except ValueError as exc:
        raise SetupError("The nuLUCA setup service is unavailable.") from exc
    if (origin.scheme != "https" or not origin.netloc or origin.username or origin.password
            or origin.path or origin.query or origin.fragment):
        raise SetupError("The nuLUCA setup service is unavailable.")
    return value


def _member_release(key: str, *, base_url: str) -> tuple[str, str]:
    """Obtain a short-lived artifact URL from nuLUCA without putting the key in it."""
    try:
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False,
                          headers={"X-API-Key": key, "Accept": "application/json"}) as client:
            response = client.get(f"{base_url}/v1/agent-connect/nuluca/member-downloads/jsontogh/{_platform()}")
        if response.status_code == 404:
            raise SetupError(f"{base_url} does not offer the member plugin download yet. "
                             "Use --environment staging with an invited pilot key.")
        if response.status_code in {401, 403}:
            raise SetupError("This key cannot download the plugin. Check that it is a Modeling key "
                             "for the same environment, on the Connected agents page.")
        if response.is_redirect or response.status_code != 200:
            raise SetupError("The member plugin download was unavailable.")
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SetupError("The member plugin download was unavailable.") from exc
    if not isinstance(body, dict):
        raise SetupError("The member plugin download was unavailable.")
    source, digest = body.get("archive_url"), body.get("sha256")
    parsed = urlsplit(source) if isinstance(source, str) else None
    if not (parsed and parsed.scheme == "https" and parsed.netloc and not parsed.username and not parsed.password
            and isinstance(digest, str) and _SHA256.fullmatch(digest.lower())):
        raise SetupError("The member plugin download was unavailable.")
    return source, digest.lower()


def _platform() -> str:
    if os.name == "nt":
        return "win"
    if os.name == "posix":
        return "mac"
    raise SetupError("This operating system is not supported for the plugin.")


def _checksum_source(source: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    parsed = urlsplit(source)
    if parsed.scheme == "https":
        return source.rsplit("/", 1)[0] + "/SHA256SUMS"
    return str(Path(source).expanduser().resolve().parent / "SHA256SUMS")


def _published_digest(source: str, checksums: str | None) -> str:
    content = _read_source(_checksum_source(source, checksums)).decode("utf-8", errors="strict")
    wanted = Path(urlsplit(source).path).name or Path(source).name
    for line in content.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and Path(parts[1].lstrip("* ")).name == wanted and _SHA256.fullmatch(parts[0].lower()):
            return parts[0].lower()
    raise SetupError("SHA256SUMS does not contain this plugin archive.")


def _safe_archive(payload: bytes, expected: str) -> dict[str, bytes]:
    if hashlib.sha256(payload).hexdigest() != expected:
        raise SetupError("The plugin archive checksum did not match SHA256SUMS.")
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(payload)) as archive:
            names = set(archive.namelist())
            allowed = set(PLUGIN_FILES) | set(ARCHIVE_DOCUMENTS)
            if not set(PLUGIN_FILES).issubset(names) or not names <= allowed:
                raise SetupError("The plugin archive has an unexpected layout.")
            for info in archive.infolist():
                if info.filename.startswith(("/", "\\")) or ".." in Path(info.filename).parts:
                    raise SetupError("The plugin archive contains an unsafe path.")
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise SetupError("The plugin archive contains a link.")
            sums = archive.read("SHA256SUMS").decode("utf-8", errors="strict")
            entries = {line.split(None, 1)[1].lstrip("* "): line.split(None, 1)[0]
                       for line in sums.splitlines() if len(line.split(None, 1)) == 2}
            files = {name: archive.read(name) for name in PLUGIN_FILES}
    except (OSError, zipfile.BadZipFile, UnicodeError) as exc:
        raise SetupError("The plugin archive is invalid.") from exc
    for name, content in files.items():
        if entries.get(name, "").lower() != hashlib.sha256(content).hexdigest():
            raise SetupError("The plugin file checksum did not match SHA256SUMS.")
    return files


def _clear_download_block(path: Path) -> None:
    if os.name == "nt":
        try:
            Path(f"{path}:Zone.Identifier").unlink()
        except OSError:
            pass
    else:
        try:
            os.removexattr(path, "com.apple.quarantine")
        except (AttributeError, OSError):
            pass


def _replace_file(destination: Path, content: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=destination.parent, prefix=f".{destination.name}.",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except OSError as exc:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise SetupError(f"Setup could not replace {destination.name}. Rhino may be using it. "
                         "Ask the member to close Rhino, then run setup again.") from exc


def _copy_plugin(files: dict[str, bytes], *, dry_run: bool,
                 state: dict) -> tuple[dict[str, str], dict[str, str], list[str]]:
    directory = _libraries_dir()
    previous = state.get("plugin_paths", {}) if isinstance(state.get("plugin_paths"), dict) else {}
    restore = state.get("plugin_restore", {}) if isinstance(state.get("plugin_restore"), dict) else {}
    result: dict[str, str] = {}
    notes: list[str] = []
    if not dry_run:
        directory.mkdir(parents=True, exist_ok=True)
        for value in previous.values():
            if Path(value).parent in _stale_libraries_dirs():
                Path(value).unlink(missing_ok=True)
    for name, content in files.items():
        destination = directory / name
        owned = name in restore or (name in previous and Path(previous[name]) == destination)
        if name in SHARED_FILES and destination.exists() and not owned:
            notes.append(f"Kept the {name} already in Grasshopper Libraries; other plugins may share it.")
            continue
        result[name] = str(destination)
        if dry_run:
            continue
        if destination.exists() and destination.read_bytes() == content:
            continue
        if destination.exists() and not owned:
            backup = _backup_dir() / name
            _secure_dir(backup.parent)
            shutil.copy2(destination, backup)
            restore[name] = str(backup)
        _replace_file(destination, content)
        _clear_download_block(destination)
    return result, restore, notes


def _server_entry(package_spec: str | None) -> tuple[str, list[str]]:
    """The command a client can start later, after this setup process is gone.

    `uvx` removes its environment's bin directory from PATH when it exits, so the
    bare console script does not exist for the client. The entry names uvx itself
    and pins the version that ran setup, because this process holds the member key.
    """
    uvx = shutil.which("uvx")
    if uvx:
        if package_spec:
            return uvx, ["--from", package_spec, PLUGIN_NAME, "serve"]
        return uvx, [f"{PLUGIN_NAME}@{version(PLUGIN_NAME)}", "serve"]
    installed = shutil.which(PLUGIN_NAME)
    if installed:
        return installed, ["serve"]
    raise SetupError("Install uv so your coding client can start nuluca-modeling, then run setup again.")


def detected_clients() -> tuple[str, ...]:
    found = []
    if shutil.which("claude") or _claude_config().exists():
        found.append("claude")
    if shutil.which("codex") or _codex_config().exists():
        found.append("codex")
    return tuple(found) or (OTHER_CLIENT,)


def _manual_entry(command: str, args: list[str], *, base_url: str) -> dict:
    return {"transport": "stdio", "command": command, "args": args, "env": {
        "NULUCA_AGENT_CONNECT_URL": base_url,
        "NULUCA_AGENT_CONNECT_KEY": "<Modeling key, stored in your client's protected credential entry>",
    }}


def _claude_entry(key: str, command: str, args: list[str], *, base_url: str) -> dict:
    return {"command": command, "args": args, "env": {
        "NULUCA_AGENT_CONNECT_URL": base_url, "NULUCA_AGENT_CONNECT_KEY": key,
    }}


def _configure_claude(key: str, command: str, args: list[str], *, base_url: str, dry_run: bool) -> Path:
    path = _claude_config()
    try:
        document = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError) as exc:
        raise SetupError("Claude Code configuration could not be read safely.") from exc
    if not isinstance(document, dict):
        raise SetupError("Claude Code configuration could not be read safely.")
    servers = document.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise SetupError("Claude Code MCP configuration could not be updated safely.")
    servers[PLUGIN_NAME] = _claude_entry(key, command, args, base_url=base_url)
    if not dry_run:
        _atomic_write(path, json.dumps(document, indent=2, sort_keys=True) + "\n")
    return path


def _toml(value: str) -> str:
    return json.dumps(value)


def _configure_codex(key: str, command: str, args: list[str], *, base_url: str, dry_run: bool) -> Path:
    path = _codex_config()
    existing = path.read_text() if path.exists() else ""
    # A server table continues until the next table. Remove this exact server and
    # its nested env table so a rerun is an upgrade, never a second registration.
    pattern = r"(?ms)^\[mcp_servers\.nuluca-modeling(?:\.env)?\]\n.*?(?=^\[|\Z)"
    existing = re.sub(pattern, "", existing).rstrip()
    block = "\n".join((
        "[mcp_servers.nuluca-modeling]",
        f"command = {_toml(command)}",
        f"args = {json.dumps(args)}",
        "",
        "[mcp_servers.nuluca-modeling.env]",
        f"NULUCA_AGENT_CONNECT_URL = {_toml(base_url)}",
        f"NULUCA_AGENT_CONNECT_KEY = {_toml(key)}",
        "",
    ))
    if not dry_run:
        _atomic_write(path, (existing + "\n\n" if existing else "") + block)
    return path


def _load_state() -> dict:
    path = _state_file()
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise SetupError("The previous setup record could not be read safely.") from exc
    return state if isinstance(state, dict) else {}


def setup(options: SetupOptions) -> dict:
    if not options.key.startswith(("ae_test_", "ae_live_")):
        raise SetupError("A nuLUCA Modeling key is required in NULUCA_AGENT_CONNECT_KEY.")
    base_url = _service_url(options.environment)
    command, args = _server_entry(options.package_spec)
    state = _load_state()
    if options.source:
        expected = _published_digest(options.source, options.checksums)
        payload = _read_source(options.source)
    else:
        if options.checksums:
            raise SetupError("--checksums-source requires --plugin-source.")
        source, expected = _member_release(options.key, base_url=base_url)
        payload = _read_source(source)
    plugin_files = _safe_archive(payload, expected)
    plugin_paths, restore, notes = _copy_plugin(plugin_files, dry_run=options.dry_run, state=state)
    configs: dict[str, str] = {}
    for client in options.clients:
        if client == "claude":
            path = _configure_claude(options.key, command, args, base_url=base_url, dry_run=options.dry_run)
            configs[client] = str(path)
        elif client == "codex":
            path = _configure_codex(options.key, command, args, base_url=base_url, dry_run=options.dry_run)
            configs[client] = str(path)
    if not options.dry_run:
        _atomic_write(_state_file(), json.dumps({"plugin_paths": plugin_paths, "plugin_restore": restore,
                                                  "clients": list(options.clients)}, indent=2) + "\n")
    result = {"service": base_url, "plugin": plugin_paths, "clients": configs, "restart": RESTART_STEP}
    if notes:
        result["notes"] = notes
    if OTHER_CLIENT in options.clients:
        result["manual_client_entry"] = _manual_entry(command, args, base_url=base_url)
    return result


def uninstall(*, dry_run: bool = False) -> dict:
    state = _load_state()
    removed: list[str] = []
    for value in state.get("plugin_paths", {}).values():
        path = Path(value)
        if (path.name in PLUGIN_FILES and path.parent in (_libraries_dir(), *_stale_libraries_dirs())
                and path.exists()):
            if not dry_run:
                path.unlink()
            removed.append(str(path))
    for name, value in state.get("plugin_restore", {}).items():
        destination = _libraries_dir() / name
        backup = Path(value)
        if name in PLUGIN_FILES and backup.is_file():
            if not dry_run:
                shutil.copy2(backup, destination)
                backup.unlink()
            removed.append(str(destination))
    for client in state.get("clients", []):
        if client not in CONFIGURED_CLIENTS:
            continue
        path = _claude_config() if client == "claude" else _codex_config()
        if not path.exists():
            continue
        content = path.read_text()
        if client == "claude":
            try:
                document = json.loads(content)
                if isinstance(document.get("mcpServers"), dict):
                    document["mcpServers"].pop(PLUGIN_NAME, None)
                    content = json.dumps(document, indent=2, sort_keys=True) + "\n"
            except ValueError:
                raise SetupError("Claude Code configuration could not be read safely.")
        else:
            content = re.sub(r"(?ms)^\[mcp_servers\.nuluca-modeling(?:\.env)?\]\n.*?(?=^\[|\Z)", "", content).strip() + "\n"
        if not dry_run:
            _atomic_write(path, content)
        removed.append(str(path))
    if not dry_run:
        _state_file().unlink(missing_ok=True)
    return {"removed": removed}
