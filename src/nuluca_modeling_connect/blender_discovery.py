"""Find the member's Blender and read its version. Nothing here installs or opens an application."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

MINIMUM = (4, 2)
TESTED = {(5, 2)}
_PROBE_SECONDS = 30
_VERSION = re.compile(r"^Blender (\d+)\.(\d+)(?:\.(\d+))?(?:\s+(.*))?$", re.MULTILINE)
_BUILD = re.compile(r"build hash:\s*([0-9a-f]+)")
_APP_ID = 'kMDItemCFBundleIdentifier == "org.blenderfoundation.blender"'


@dataclass(frozen=True)
class Install:
    path: str
    root: str
    source: str
    version: str
    numbers: tuple[int, ...]
    build_hash: str | None
    support: str

    def public(self) -> dict:
        record = asdict(self)
        record.pop("numbers")
        return record


def executable(path: Path, system: str) -> Path | None:
    """The binary for a user-supplied or discovered path, including a macOS .app bundle."""
    if system == "Darwin" and path.suffix == ".app":
        for name in ("Blender", "blender"):
            if (path / "Contents" / "MacOS" / name).is_file():
                return path / "Contents" / "MacOS" / name
        return None
    if path.is_dir():
        name = "blender.exe" if system == "Windows" else "blender"
        return path / name if (path / name).is_file() else None
    return path if path.is_file() else None


def _install_root(binary: Path, system: str) -> Path:
    if system == "Darwin" and binary.parent.name == "MacOS" and binary.parent.parent.name == "Contents":
        return binary.parent.parent.parent
    return binary.parent


def _spotlight() -> list[str]:
    try:
        result = subprocess.run(["mdfind", _APP_ID], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    return [line for line in result.stdout.splitlines() if line.endswith(".app")]


def _registry() -> list[str]:
    try:
        import winreg
        command = winreg.QueryValue(winreg.HKEY_CLASSES_ROOT, r"blendfile\shell\open\command")
    except (ImportError, OSError):
        return []
    return [command]


def _from_open_command(command: str) -> Path | None:
    """The file association names blender-launcher.exe; blender.exe sits beside it."""
    match = re.match(r'\s*"([^"]+)"|\s*(\S+)', command)
    if match is None:
        return None
    launcher = Path(match.group(1) or match.group(2))
    return launcher.with_name("blender.exe")


def candidates(system: str, env: dict, home: Path, *, which=shutil.which, spotlight=_spotlight,
               registry=_registry) -> list[tuple[Path, str]]:
    """Likely locations, most specific first. Paths that do not exist are dropped by the caller."""
    found: list[tuple[Path, str]] = []
    on_path = which("blender")
    if on_path:
        found.append((Path(on_path), "path"))
    if system == "Darwin":
        for root in (Path("/Applications"), home / "Applications"):
            found += [(item, "applications") for item in sorted(root.glob("Blender*.app"), reverse=True)]
        found.append((home / "Library/Application Support/Steam/steamapps/common/Blender/Blender.app", "steam"))
        found += [(Path(item), "spotlight") for item in spotlight()]
        found += [(Path("/opt/homebrew/bin/blender"), "homebrew"), (Path("/usr/local/bin/blender"), "homebrew")]
    elif system == "Windows":
        roots = [env.get(key) for key in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)")]
        local = env.get("LOCALAPPDATA")
        for root in [*roots, str(Path(local) / "Programs") if local else None]:
            if root:
                found += [(item / "blender.exe", "program files") for item in
                          sorted((Path(root) / "Blender Foundation").glob("Blender *"), reverse=True)]
                found.append((Path(root) / "Steam/steamapps/common/Blender/blender.exe", "steam"))
        found += [(path, "file association") for path in map(_from_open_command, registry()) if path]
        if local:
            found.append((Path(local) / "Microsoft/WindowsApps/blender.exe", "microsoft store"))
        found.append((home / "scoop/apps/blender/current/blender.exe", "scoop"))
    else:
        found += [(Path("/snap/bin/blender"), "snap"), (Path("/usr/bin/blender"), "system")]
        found += [(item / "blender", "opt") for item in sorted(Path("/opt").glob("blender*"), reverse=True)]
    return found


def support_for(numbers: tuple[int, ...]) -> str:
    if numbers[:2] in TESTED:
        return "tested"
    return "untested" if numbers[:2] >= MINIMUM else "too_old"


def parse_version(output: str) -> tuple[str, tuple[int, ...], str | None] | None:
    match = _VERSION.search(output)
    if match is None:
        return None
    numbers = tuple(int(part) for part in match.groups()[:3] if part is not None)
    build = _BUILD.search(output)
    return match.group(0).strip(), numbers, build.group(1) if build else None


def probe(binary: Path, source: str, system: str, env: dict) -> Install | str:
    """Read one binary's version in a private profile. Returns the reason on failure."""
    try:
        output = subprocess.run([str(binary), "--version"], capture_output=True, text=True, env=env,
                                timeout=_PROBE_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not start: {type(exc).__name__}"
    parsed = parse_version(output.stdout)
    if output.returncode != 0 or parsed is None:
        return "no Blender version was reported"
    version, numbers, build = parsed
    return Install(str(binary), str(_install_root(binary, system)), source, version, numbers, build,
                   support_for(numbers))


def discover(env: dict, probe_env: dict, *, system: str | None = None, home: Path | None = None,
             search=candidates, read=probe) -> dict:
    """Choose the newest tested Blender, else the newest usable one.

    ``NULUCA_BLENDER_PATH`` wins. If it is wrong the result says so; another copy is never
    substituted for the one the member named.
    """
    system = system or platform.system()
    home = home or Path.home()
    named = env.get("NULUCA_BLENDER_PATH")
    pairs = [(Path(named).expanduser(), "NULUCA_BLENDER_PATH")] if named else search(system, env, home)
    installs: list[Install] = []
    rejected: list[dict] = []
    seen: set[str] = set()
    for path, source in pairs:
        binary = executable(path, system)
        if binary is None:
            if named:
                rejected.append({"path": str(path), "source": source, "reason": "no Blender executable there"})
            continue
        key = os.path.normcase(str(binary.resolve()))
        if key in seen:
            continue
        seen.add(key)
        found = read(binary, source, system, probe_env)
        if isinstance(found, Install):
            installs.append(found)
        else:
            rejected.append({"path": str(binary), "source": source, "reason": found})
    usable = [item for item in installs if item.support != "too_old"]
    usable.sort(key=lambda item: (item.support == "tested", item.numbers), reverse=True)
    return {"selected": usable[0] if usable else None, "installs": installs, "rejected": rejected}
