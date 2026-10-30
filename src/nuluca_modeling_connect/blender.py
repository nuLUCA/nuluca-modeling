"""Local Blender surface for a member's own agent.

The wheel starts its own background Blender for each call. It never touches a Blender
the member has open, and it stops only a child it started, through that child's own
process handle. Member scripts run as the member, so the guard in ``blender_guard.py``
is a guard rail and not a sandbox.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import platform
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from . import blender_memory as memory
from .blender_discovery import Install, discover
from .bridge import BridgeError

_PASSED_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR", "USERPROFILE", "SYSTEMDRIVE", "PATHEXT",
               "COMSPEC", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE")
_IN_BLENDER = ("blender_guard.py", "blender_steps.py", "nuluca_author.py")
_RUNS = "blender-runs"
_SOURCE_LIMIT = 256 * 1024
_MIN_FREE_BYTES = 2 * 1024 ** 3
_RUN_TIMEOUT = 3600
_RUN_TIMEOUT_MAX = 6 * 3600
_STEP_TIMEOUT = 300
_WAIT_DEFAULT = 40
_POLL_SECONDS = 0.25
_SAMPLE_SECONDS = 2.0
_STOP_GRACE_SECONDS = 5
_LOG_TAIL = 2000
_MAX_ARGS = 32
_MAX_CASES = 12
_MAX_ARTIFACTS = 500
_MAX_INLINE_IMAGES = 3
_MAX_IMAGE_SIDE = 2000
_MAX_MEMORY_GB = 512.0
_MAX_RESULT_BYTES = 8 * 1024 * 1024
_MAX_IMAGE_BYTES = 16 * 1024 * 1024
_PNG = b"\x89PNG\r\n\x1a\n"
_CASE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_RESERVED_NAME = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])$", re.IGNORECASE)
_IMAGE_NAME = re.compile(r"^[a-z]+\.png$")
_VIEWS = ("iso", "front", "back", "left", "right", "top", "bottom")
_FORMATS = ("glb", "usd", "stl", "obj")
_FILE = {"type": "string", "description": "Path relative to the modeling work folder."}
_WAIT = {"type": "number", "description": "Seconds to wait before returning with status running. Default 40."}


class BlenderError(BridgeError):
    pass


def _schema(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


_FIXED_TOOLS = [
    {"name": "blender_status",
     "description": "Find the member's Blender and report version support, the work folder and memory guards. "
                    "With run, report that run and its result.",
     "inputSchema": _schema({"run": {"type": "string"}, "refresh": {"type": "boolean"}})},
    {"name": "blender_guide",
     "description": "Read nuLUCA's guidance for modeling in Blender: the working loop, file handling and checks.",
     "inputSchema": _schema({})},
    {"name": "blender_capabilities",
     "description": "List this Blender's Python, exporter settings and Geometry Nodes types.",
     "inputSchema": _schema({"wait_seconds": _WAIT})},
    {"name": "blender_cancel", "description": "Stop a run this server started.",
     "inputSchema": _schema({"run": {"type": "string"}}, ("run",))},
    {"name": "blender_inspect",
     "description": "Open a .blend in the background and report objects, units, controls, evaluated counts, "
                    "bounds, materials and mesh health. Saves nothing.",
     "inputSchema": _schema({"file": _FILE, "wait_seconds": _WAIT}, ("file",))},
    {"name": "blender_vary",
     "description": "Reopen a .blend once per case, set named Geometry Nodes controls, measure, save a copy, "
                    "reopen it and compare. The source file is not changed.",
     "inputSchema": _schema({"file": _FILE, "object": {"type": "string"},
                             "cases": {"type": "object", "description": "Case name to {control: value}."},
                             "wait_seconds": _WAIT}, ("file", "object", "cases"))},
    {"name": "blender_export",
     "description": "Write a static glb, usd, stl or obj beside the run. A GLB is imported again and its bounds "
                    "compared. The receipt lists what the format keeps and loses.",
     "inputSchema": _schema({"file": _FILE, "format": {"type": "string", "enum": list(_FORMATS)},
                             "object": {"type": "string"}, "wait_seconds": _WAIT}, ("file", "format"))},
    {"name": "blender_preview",
     "description": "Render quick shaded views of a .blend with the Workbench engine and return them as images. "
                    "These are review views for the agent. Controls apply to this preview only.",
     "inputSchema": _schema({"file": _FILE, "views": {"type": "array", "items": {"type": "string",
                                                                                "enum": list(_VIEWS)}},
                             "width": {"type": "integer"}, "height": {"type": "integer"},
                             "object": {"type": "string"}, "controls": {"type": "object"},
                             "wait_seconds": _WAIT}, ("file",))},
]
_AUTHOR_TOOLS = [
    {"name": "blender_write",
     "description": "Save a Python script for Blender in the modeling work folder.",
     "inputSchema": _schema({"file": _FILE, "source": {"type": "string"}}, ("file", "source"))},
    {"name": "blender_run",
     "description": "Run a Python script from the modeling work folder in a new background Blender. This executes "
                    "code on the member's machine. The script's first argument is its artifacts folder, followed "
                    "by inputs and args. Show the member a script you did not write before running it.",
     "annotations": {"title": "Run Python in Blender", "readOnlyHint": False, "destructiveHint": True,
                     "openWorldHint": False},
     "inputSchema": _schema({"script": _FILE,
                             "inputs": {"type": "array", "items": {"type": "string"},
                                        "description": "Files in the work folder, hashed into the receipt."},
                             "args": {"type": "array", "items": {"type": "string"}},
                             "timeout_seconds": {"type": "number"}, "memory_limit_gb": {"type": "number"},
                             "wait_seconds": _WAIT}, ("script",))},
]


def _mode(env) -> str:
    """``off`` removes every Blender tool. ``fixed`` removes the two that run member Python."""
    value = env.get("NULUCA_MODELING_BLENDER", "on").strip().lower()
    return value if value in {"on", "fixed", "off"} else "off"


def blender_tools(env=None) -> list[dict]:
    mode = _mode(os.environ if env is None else env)
    if mode == "off":
        return []
    return [*_FIXED_TOOLS, *(_AUTHOR_TOOLS if mode == "on" else [])]


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _open_regular(path: Path):
    """Open a file a script may have replaced. Links, pipes and devices are refused."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        if path.is_symlink():
            return None
        stream = os.fdopen(os.open(path, flags), "rb")
    except OSError:
        return None
    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
        stream.close()
        return None
    return stream


def _read_regular(path: Path, limit: int, *, tail: bool = False) -> bytes | None:
    stream = _open_regular(path)
    if stream is None:
        return None
    with stream:
        size = os.fstat(stream.fileno()).st_size
        if tail:
            stream.seek(max(0, size - limit))
        elif size > limit:
            return None
        return stream.read(limit)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Run:
    def __init__(self, run_id: str, folder: Path, kind: str, timeout: float, limit_gb: float) -> None:
        self.id, self.folder, self.kind = run_id, folder, kind
        self.timeout, self.limit_gb = timeout, limit_gb
        self.done = threading.Event()
        self.process: subprocess.Popen | None = None
        self.stop_reason: str | None = None
        self.receipt: dict = {}
        self.result: dict | None = None
        self.images: list[str] = []
        self.started = time.monotonic()


class BlenderTools:
    def __init__(self, *, root: Path | None = None, env: dict | None = None, finder=discover,
                 read_memory=memory.commit_percent) -> None:
        self.env = dict(os.environ if env is None else env)
        self.root = (root or Path(self.env.get("NULUCA_MODELING_WORK_ROOT", "~/nuluca-modeling/work"))).expanduser().resolve()
        self.finder = finder
        self.read_memory = read_memory
        self._found: dict | None = None
        self._lock = threading.Lock()
        self._active: _Run | None = None
        self._runs: dict[str, _Run] = {}

    # Paths -----------------------------------------------------------------

    def _path(self, value: object, suffixes: tuple[str, ...]) -> Path:
        if not isinstance(value, str) or not value or not value.endswith(suffixes):
            raise BlenderError(f"A {' or '.join(suffixes)} file in the modeling work folder is required.")
        path = (self.root / value).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise BlenderError("The file must stay inside the modeling work folder.")
        return path

    def _existing(self, value: object, suffixes: tuple[str, ...]) -> Path:
        path = self._path(value, suffixes)
        if not path.is_file():
            raise BlenderError(f"{value} is not in the modeling work folder.")
        return path

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    # Environment -----------------------------------------------------------

    def _child_env(self, folder: Path, install_root: str) -> dict:
        """A short allow-list. The Agent Connect key and every other variable stay out of Blender."""
        env = {key: value for key, value in self.env.items() if key in _PASSED_ENV}
        for variable, name in (("BLENDER_USER_CONFIG", "runtime-config"), ("BLENDER_USER_SCRIPTS", "runtime-scripts"),
                               ("BLENDER_USER_DATAFILES", "runtime-datafiles")):
            owned = folder / name
            owned.mkdir(parents=True, exist_ok=True)
            env[variable] = str(owned)
        temporary = folder / "runtime-tmp"
        temporary.mkdir(parents=True, exist_ok=True)
        env.update({"TMPDIR": str(temporary), "TEMP": str(temporary), "TMP": str(temporary),
                    "PYTHONDONTWRITEBYTECODE": "1", "NULUCA_BLENDER_WORK_ROOT": str(self.root),
                    "NULUCA_BLENDER_MEMBER_HOME": str(Path.home()), "NULUCA_BLENDER_INSTALL_ROOT": install_root,
                    "NULUCA_BLENDER_PARENT": str(os.getpid())})
        return env

    def _discover(self, refresh: bool = False) -> dict:
        with self._lock:
            if self._found is None or refresh:
                self.root.mkdir(parents=True, exist_ok=True)
                probe_env = self._child_env(self.root / _RUNS / "probe", str(self.root))
                self._found = self.finder(self.env, probe_env)
            return self._found

    def _install(self) -> Install:
        found = self._discover()
        if found["selected"] is None:
            too_old = [item.version for item in found["installs"]]
            detail = f" Found {', '.join(too_old)}, older than 4.2." if too_old else ""
            raise BlenderError("No usable Blender was found." + detail + " Install Blender 4.2 or newer from "
                               "blender.org, or set NULUCA_BLENDER_PATH, then call blender_status with refresh.")
        return found["selected"]

    # Launch ----------------------------------------------------------------

    def _start(self, kind: str, payload: bytes, trailing: list[str], *, timeout: float, limit_gb: float,
               extra: dict | None = None, request: dict | None = None) -> _Run:
        install = self._install()
        if shutil.disk_usage(self.root).free < _MIN_FREE_BYTES:
            raise BlenderError("Less than 2 GB of disk is free. Nothing was started.")
        gate = memory.wait_for_commit(read=self.read_memory)
        run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{kind}-{uuid.uuid4().hex[:6]}"
        folder = self.root / _RUNS / run_id
        receipt = {"schema_version": 1, "run": run_id, "kind": kind, "runtime": "Blender local",
                   "blender": install.public(), "host": platform.platform(), "commit_gate": gate,
                   "authority": "agent-authored local source; no security isolation claim",
                   "started_at": _now(), "status": "running", **(extra or {})}
        with self._lock:
            if self._active is not None:
                raise BlenderError(f"Blender run {self._active.id} is still going. Wait for it or cancel it.")
            run = self._runs[run_id] = _Run(run_id, folder, kind, timeout, limit_gb)
            self._active = run
        try:
            artifacts = folder / "artifacts"
            artifacts.mkdir(parents=True)
            if request is not None:
                (folder / "request.json").write_text(json.dumps(request), encoding="utf-8")
                trailing = [kind, str(folder / "request.json")]
            self._launch(run, install, payload, trailing, receipt)
        except BaseException:
            with self._lock:
                self._active = None
            raise
        return run

    def _launch(self, run: _Run, install: Install, payload: bytes, trailing: list[str], receipt: dict) -> None:
        folder = run.folder
        artifacts = folder / "artifacts"
        env = self._child_env(folder, install.root)
        modules = folder / "runtime-modules"
        modules.mkdir()
        package = resources.files(__package__)
        guard = folder / "guard.py"
        guard.write_bytes(package.joinpath("blender_guard.py").read_bytes())
        (modules / "nuluca_author.py").write_bytes(package.joinpath("nuluca_author.py").read_bytes())
        source = folder / "source.py"
        source.write_bytes(payload)
        command = [install.path, "--background", "--factory-startup", "--disable-autoexec", "--offline-mode",
                   "--python-exit-code", "1", "--python", str(guard), "--python", str(source), "--",
                   str(artifacts), *trailing]
        receipt.update({"source_sha256": _sha(source), "guard_sha256": _sha(guard), "command": command,
                        "memory_limit_gb": run.limit_gb})
        run.receipt = receipt
        if not receipt["commit_gate"]["ok"]:
            self._finish(run, "halted_commit_gate", None)
            return
        self._write_receipt(run)
        job = memory.ProcessMemoryJob(run.limit_gb)
        log = (folder / "process.log").open("w")
        try:
            run.process = subprocess.Popen(command, cwd=folder, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                           stderr=subprocess.STDOUT, creationflags=job.creationflags())
        except BaseException:
            job.close()
            log.close()
            raise
        run.started = time.monotonic()
        try:
            receipt["memory_job"] = job.assign(run.process)
            threading.Thread(target=self._monitor, args=(run, job, log), daemon=True).start()
        except BaseException:
            self._stop(run.process)
            job.close()
            log.close()
            raise

    def _monitor(self, run: _Run, job: memory.ProcessMemoryJob, log) -> None:
        process = run.process
        peak = 0
        try:
            peak = self._watch(run, process)
            code = process.wait()
        except Exception as exc:
            run.stop_reason = run.stop_reason or "monitor_failed_artifacts_preserved"
            run.receipt["monitor_error"] = type(exc).__name__
            self._stop(process)
            code = process.poll()
        finally:
            log.close()
        run.receipt["memory_job_peak_bytes"] = job.peak_bytes()
        job.close()
        run.receipt["peak_rss_bytes"] = self._reported_peak(run) or peak or None
        self._finish(run, run.stop_reason or ("process_completed" if code == 0
                                              else "process_failed_artifacts_preserved"), code)

    def _watch(self, run: _Run, process: subprocess.Popen) -> int:
        peak = 0
        last_sample = 0.0
        while process.poll() is None:
            now = time.monotonic()
            if run.stop_reason is None and now - run.started > run.timeout:
                run.stop_reason = "timed_out_artifacts_preserved"
            if run.stop_reason is None and now - last_sample >= _SAMPLE_SECONDS:
                last_sample = now
                rss = memory.process_rss_bytes(process.pid)
                peak = max(peak, rss or 0)
                used = self.read_memory()
                if rss is not None and run.limit_gb > 0 and rss > run.limit_gb * 1024 ** 3:
                    run.stop_reason = "stopped_memory_limit_artifacts_preserved"
                elif used is not None and used > memory.COMMIT_STOP_DEFAULT:
                    run.stop_reason = "stopped_low_memory_artifacts_preserved"
            if run.stop_reason is not None:
                self._stop(process)
                break
            time.sleep(_POLL_SECONDS)
        return peak

    @staticmethod
    def _reported_peak(run: _Run) -> int | None:
        """The guard records Blender's own high-water mark at exit. A stopped run has only the samples."""
        try:
            return int(_read_regular(run.folder / "peak-rss-bytes.txt", 32) or b"")
        except ValueError:
            return None

    @staticmethod
    def _stop(process: subprocess.Popen) -> None:
        """Stop the child this server started. Member applications are out of reach here."""
        process.terminate()
        try:
            process.wait(_STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()

    def _intact(self, run: _Run) -> bool:
        """A script can move folders inside the work folder. Nothing is read or written through a swapped path."""
        try:
            return run.folder.resolve() == self.root / _RUNS / run.id and not run.folder.is_symlink()
        except OSError:
            return False

    def _write_receipt(self, run: _Run) -> None:
        if not self._intact(run):
            return
        target = run.folder / "execution.json"
        temporary = run.folder / f".execution-{uuid.uuid4().hex}.json"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(temporary, flags, 0o600), "w", encoding="utf-8") as stream:
            json.dump(run.receipt, stream, indent=2)
        os.replace(temporary, target)

    def _artifact_records(self, run: _Run) -> tuple[list[dict], bool]:
        base = run.folder / "artifacts"
        if not self._intact(run) or base.is_symlink():
            return [], False
        names = sorted(Path(folder) / name for folder, _dirs, files in os.walk(base) for name in files)
        records = []
        for path in names[:_MAX_ARTIFACTS]:
            stream = _open_regular(path)
            if stream is None:
                continue
            digest = hashlib.sha256()
            with stream:
                try:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                    records.append({"path": self._relative(path), "bytes": stream.tell(),
                                    "sha256": digest.hexdigest()})
                except OSError:
                    continue
        return records, len(names) > _MAX_ARTIFACTS

    def _collect_result(self, run: _Run) -> None:
        """Read a fixed step's output once, when it ends. Later scripts can rewrite the run folder."""
        if run.kind == "script" or not self._intact(run):
            return
        artifacts = run.folder / "artifacts"
        try:
            run.result = json.loads(_read_regular(artifacts / "result.json", _MAX_RESULT_BYTES) or b"null")
        except ValueError:
            run.result = None
        images = run.result.get("images") if run.kind == "preview" and isinstance(run.result, dict) else None
        for item in (images or [])[:_MAX_INLINE_IMAGES]:
            name = item.get("file") if isinstance(item, dict) else None
            data = _read_regular(artifacts / name, _MAX_IMAGE_BYTES) if (
                isinstance(name, str) and _IMAGE_NAME.fullmatch(name)) else None
            if data and data.startswith(_PNG):
                run.images.append(base64.b64encode(data).decode())

    def _finish(self, run: _Run, status: str, code: int | None) -> None:
        receipt = run.receipt
        receipt.update({"status": status, "exit_code": code, "finished_at": _now(),
                        "elapsed_seconds": round(time.monotonic() - run.started, 3)})
        try:
            receipt["artifacts"], receipt["artifacts_truncated"] = self._artifact_records(run)
            self._collect_result(run)
            self._write_receipt(run)
        except Exception as exc:
            receipt["finish_error"] = type(exc).__name__
        finally:
            with self._lock:
                if self._active is run:
                    self._active = None
            run.done.set()

    def close(self) -> None:
        """Called when the server stops, so no background Blender outlives it."""
        run = self._active
        if run is not None and run.process is not None:
            run.stop_reason = "cancelled_artifacts_preserved"
            run.done.wait(_STOP_GRACE_SECONDS * 2)

    # Results ---------------------------------------------------------------

    def _report(self, run: _Run) -> dict:
        if not run.done.is_set():
            return {"run": run.id, "status": "running", "elapsed_seconds": round(time.monotonic() - run.started, 1),
                    "log_tail": self._log_tail(run)}
        receipt = run.receipt
        report = {key: receipt.get(key) for key in ("run", "kind", "status", "exit_code", "elapsed_seconds",
                                                    "peak_rss_bytes", "memory_job_peak_bytes", "memory_job",
                                                    "artifacts", "artifacts_truncated", "finish_error")}
        report["receipt"] = self._relative(run.folder / "execution.json")
        if run.kind != "script":
            report["result"] = run.result
        if run.images:
            report["_images"] = list(run.images)
        if receipt.get("status") != "process_completed":
            report["log_tail"] = self._log_tail(run)
        return report

    def _log_tail(self, run: _Run) -> str:
        if not self._intact(run):
            return ""
        return (_read_regular(run.folder / "process.log", _LOG_TAIL, tail=True) or b"").decode("utf-8", "replace")

    def _find_run(self, run_id: object) -> _Run:
        run = self._runs.get(run_id) if isinstance(run_id, str) else None
        if run is None:
            raise BlenderError("This server has no run with that id. Finished runs keep execution.json on disk.")
        return run

    @staticmethod
    def _number(value: object, name: str, low: float, high: float) -> float:
        if type(value) not in (int, float) or not math.isfinite(value) or value < low:
            raise BlenderError(f"{name} must be a number of at least {low:g}.")
        return min(float(value), high)

    def _wait_time(self, args: dict) -> float:
        return self._number(args.get("wait_seconds", _WAIT_DEFAULT), "wait_seconds", 0, 600)

    def _step(self, step: str, request: dict, args: dict, *, inputs: tuple[Path, ...] = ()) -> dict:
        code = resources.files(__package__).joinpath("blender_steps.py").read_bytes()
        request = {**request, "file": str(inputs[0])} if inputs else request
        run = self._start(step, code, [], timeout=_STEP_TIMEOUT, limit_gb=memory.default_limit_gb(), request=request,
                          extra={"dependencies": [{"path": self._relative(item), "sha256": _sha(item),
                                                   "bytes": item.stat().st_size} for item in inputs]})
        run.done.wait(self._wait_time(args))
        return self._report(run)

    # Tools -----------------------------------------------------------------

    def _status(self, args: dict) -> dict:
        if args.get("run") is not None:
            return self._report(self._find_run(args["run"]))
        found = self._discover(bool(args.get("refresh")))
        selected = found["selected"]
        active = self._active
        return {"selected": selected.public() if selected else None,
                "installs": [item.public() for item in found["installs"]], "rejected": found["rejected"],
                "work_folder": str(self.root), "python_scripts": _mode(self.env) == "on",
                "memory": {"percent_in_use": memory.commit_percent(), "gate_percent": memory.COMMIT_GATE_DEFAULT,
                           "stop_percent": memory.COMMIT_STOP_DEFAULT, "ceiling_gb": memory.default_limit_gb()},
                "active_run": active.id if active else None}

    def _write(self, args: dict) -> dict:
        path = self._path(args.get("file"), (".py",))
        source = args.get("source")
        if not isinstance(source, str) or not source or len(source.encode()) > _SOURCE_LIMIT:
            raise BlenderError("The script is empty or larger than 256 KB.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        return {"file": self._relative(path), "bytes": path.stat().st_size, "sha256": _sha(path)}

    def _run_script(self, args: dict) -> dict:
        script = self._existing(args.get("script"), (".py",))
        inputs = args.get("inputs") or []
        extra = args.get("args") or []
        if (not isinstance(inputs, list) or not isinstance(extra, list) or len(inputs) + len(extra) > _MAX_ARGS
                or not all(isinstance(item, str) and len(item) <= 1024 for item in extra)):
            raise BlenderError("inputs and args must be short lists of strings.")
        files = [(self.root / item).resolve() if isinstance(item, str) else None for item in inputs]
        if not all(path and path.is_file() and path.is_relative_to(self.root) for path in files):
            raise BlenderError("Every input must be a file in the modeling work folder.")
        timeout = self._number(args.get("timeout_seconds", _RUN_TIMEOUT), "timeout_seconds", 0.1, _RUN_TIMEOUT_MAX)
        ceiling = self._number(args.get("memory_limit_gb", memory.default_limit_gb()), "memory_limit_gb", 1e-6,
                               _MAX_MEMORY_GB)
        record = {"script": self._relative(script), "dependencies": [
            {"path": self._relative(item), "sha256": _sha(item), "bytes": item.stat().st_size} for item in files]}
        run = self._start("script", script.read_bytes(), [*(str(item) for item in files), *extra],
                          timeout=timeout, limit_gb=ceiling, extra=record)
        run.done.wait(self._wait_time(args))
        return self._report(run)

    def _cancel(self, args: dict) -> dict:
        run = self._find_run(args.get("run"))
        if not run.done.is_set():
            run.stop_reason = "cancelled_artifacts_preserved"
            run.done.wait(_STOP_GRACE_SECONDS * 2)
        return self._report(run)

    def _vary(self, args: dict) -> dict:
        native = self._existing(args.get("file"), (".blend",))
        cases = args.get("cases")
        if (not isinstance(cases, dict) or not 1 <= len(cases) <= _MAX_CASES
                or not all(_CASE_NAME.fullmatch(str(key)) and not _RESERVED_NAME.fullmatch(key)
                           and isinstance(value, dict) for key, value in cases.items())
                or len({key.casefold() for key in cases}) != len(cases)):
            raise BlenderError("cases must map 1 to 12 distinct simple names to {control: value}.")
        if not isinstance(args.get("object"), str) or not args["object"]:
            raise BlenderError("Name the object that carries the Geometry Nodes modifier.")
        return self._step("vary", {"object": args["object"], "cases": cases}, args, inputs=(native,))

    def _export(self, args: dict) -> dict:
        native = self._existing(args.get("file"), (".blend",))
        if args.get("format") not in _FORMATS:
            raise BlenderError("Choose glb, usd, stl or obj.")
        return self._step("export", {"format": args["format"], "object": args.get("object")}, args, inputs=(native,))

    def _preview(self, args: dict) -> dict:
        native = self._existing(args.get("file"), (".blend",))
        views = args.get("views") or ["iso"]
        width, height = args.get("width", 800), args.get("height", 600)
        if not isinstance(views, list) or not 1 <= len(views) <= len(_VIEWS) or not set(views) <= set(_VIEWS):
            raise BlenderError("Choose views from " + ", ".join(_VIEWS) + ".")
        if not all(type(side) is int and 64 <= side <= _MAX_IMAGE_SIDE for side in (width, height)):
            raise BlenderError("width and height must be 64 to 2000 pixels.")
        controls = args.get("controls") or {}
        if controls and not args.get("object"):
            raise BlenderError("Name the object whose controls this preview should change.")
        return self._step("preview", {"views": views, "width": width, "height": height,
                                      "object": args.get("object"), "controls": controls}, args, inputs=(native,))

    def _call(self, name: str, args: dict) -> dict:
        if name == "blender_status":
            return self._status(args)
        if name == "blender_guide":
            return {"guide": resources.files(__package__).joinpath("blender_guide.md").read_text(encoding="utf-8")}
        if name == "blender_capabilities":
            return self._step("capabilities", {}, args)
        if name == "blender_inspect":
            return self._step("inspect", {}, args, inputs=(self._existing(args.get("file"), (".blend",)),))
        if name == "blender_vary":
            return self._vary(args)
        if name == "blender_export":
            return self._export(args)
        if name == "blender_preview":
            return self._preview(args)
        if name == "blender_cancel":
            return self._cancel(args)
        if name in {"blender_write", "blender_run"} and _mode(self.env) != "on":
            raise BlenderError("Python scripts in Blender are turned off on this machine.")
        if name == "blender_write":
            return self._write(args)
        if name == "blender_run":
            return self._run_script(args)
        raise BlenderError("Unknown Blender tool.")

    async def call(self, name: str, args: dict) -> dict:
        if not isinstance(args, dict):
            raise BlenderError("Tool arguments must be an object.")
        return await asyncio.to_thread(self._call, name, args)
