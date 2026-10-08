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
from pathlib import Path
from urllib.parse import urlsplit

import httpx

PLUGIN_NAME = "nuluca-modeling"
PLUGIN_FILES = ("JsonToGH_Fresh.gha", "Newtonsoft.Json.dll")
DEFAULT_URL = "https://nuluca-backend-staging.up.railway.app"
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
    return _home() / "Library" / "Application Support" / "Grasshopper" / "Libraries"


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


def _member_release(key: str) -> tuple[str, str]:
    """Obtain a short-lived artifact URL from nuLUCA without putting the key in it."""
    origin = urlsplit(DEFAULT_URL)
    if origin.scheme != "https" or not origin.netloc or origin.username or origin.password:
        raise SetupError("The nuLUCA setup service is unavailable.")
    try:
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False,
                          headers={"X-API-Key": key, "Accept": "application/json"}) as client:
            response = client.get(f"{DEFAULT_URL}/v1/agent-connect/nuluca/member-downloads/jsontogh/{_platform()}")
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
            allowed = set(PLUGIN_FILES) | {"INSTALL.md", "LICENSE-DRAFT.txt", "SHA256SUMS"}
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


def _copy_plugin(files: dict[str, bytes], *, dry_run: bool, state: dict) -> tuple[dict[str, str], dict[str, str]]:
    directory = _libraries_dir()
    if dry_run:
        return ({name: str(directory / name) for name in PLUGIN_FILES}, {})
    _secure_dir(directory)
    result = {}
    restore = state.get("plugin_restore", {}) if isinstance(state.get("plugin_restore"), dict) else {}
    managed = set(state.get("plugin_paths", {})) if isinstance(state.get("plugin_paths"), dict) else set()
    for name, content in files.items():
        destination = directory / name
        if destination.exists() and name not in restore and name not in managed:
            backup = _backup_dir() / name
            _secure_dir(backup.parent)
            shutil.copy2(destination, backup)
            restore[name] = str(backup)
        with tempfile.NamedTemporaryFile("wb", dir=directory, prefix=f".{name}.", delete=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        os.replace(temporary, destination)
        _clear_download_block(destination)
        result[name] = str(destination)
    return result, restore


def _claude_entry(key: str) -> dict:
    return {"command": "nuluca-modeling", "args": ["serve"], "env": {
        "NULUCA_AGENT_CONNECT_URL": DEFAULT_URL, "NULUCA_AGENT_CONNECT_KEY": key,
    }}


def _configure_claude(key: str, *, dry_run: bool) -> Path:
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
    servers[PLUGIN_NAME] = _claude_entry(key)
    if not dry_run:
        _atomic_write(path, json.dumps(document, indent=2, sort_keys=True) + "\n")
    return path


def _toml(value: str) -> str:
    return json.dumps(value)


def _configure_codex(key: str, *, dry_run: bool) -> Path:
    path = _codex_config()
    existing = path.read_text() if path.exists() else ""
    # A server table continues until the next table. Remove this exact server and
    # its nested env table so a rerun is an upgrade, never a second registration.
    pattern = r"(?ms)^\[mcp_servers\.nuluca-modeling(?:\.env)?\]\n.*?(?=^\[|\Z)"
    existing = re.sub(pattern, "", existing).rstrip()
    block = "\n".join((
        "[mcp_servers.nuluca-modeling]",
        'command = "nuluca-modeling"',
        'args = ["serve"]',
        "",
        "[mcp_servers.nuluca-modeling.env]",
        f"NULUCA_AGENT_CONNECT_URL = {_toml(DEFAULT_URL)}",
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
    state = _load_state()
    if options.source:
        expected = _published_digest(options.source, options.checksums)
        payload = _read_source(options.source)
    else:
        if options.checksums:
            raise SetupError("--checksums-source requires --plugin-source.")
        source, expected = _member_release(options.key)
        payload = _read_source(source)
    plugin_files = _safe_archive(payload, expected)
    plugin_paths, restore = _copy_plugin(plugin_files, dry_run=options.dry_run, state=state)
    configs: dict[str, str] = {}
    for client in options.clients:
        path = _configure_claude(options.key, dry_run=options.dry_run) if client == "claude" else _configure_codex(options.key, dry_run=options.dry_run)
        configs[client] = str(path)
    if not options.dry_run:
        _atomic_write(_state_file(), json.dumps({"plugin_paths": plugin_paths, "plugin_restore": restore,
                                                  "clients": list(options.clients)}, indent=2) + "\n")
    return {"plugin": plugin_paths, "clients": configs, "restart": "Restart Rhino, then call nuluca_status."}


def uninstall(*, dry_run: bool = False) -> dict:
    state = _load_state()
    removed: list[str] = []
    for value in state.get("plugin_paths", {}).values():
        path = Path(value)
        if path.name in PLUGIN_FILES and path.parent == _libraries_dir() and path.exists():
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
