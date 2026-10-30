"""Memory guards for Blender runs on a member's own machine.

A check at launch cannot catch growth after launch, so a run has three guards:

1. ``wait_for_commit``: before launch, wait while memory use is above a threshold.
   If it stays there the run is not started.
2. ``ProcessMemoryJob``: on Windows the child joins a job object with a per-process
   limit, so a runaway allocation fails inside Blender.
3. ``process_rss_bytes`` and ``commit_percent``: elsewhere the wheel's monitor polls
   its own child and stops it past the limit or when the machine runs short.

These act only on the child the wheel started, through its own process handle.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

LIMIT_GB_DEFAULT = 12.0
LIMIT_SHARE_OF_RAM = 0.6
COMMIT_GATE_DEFAULT = 85.0
COMMIT_WAIT_DEFAULT = 30.0
COMMIT_STOP_DEFAULT = 95.0

if sys.platform == "win32":
    from ctypes import wintypes

    class _MEMSTATUS(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    class _IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount",
                                                      "OtherOperationCount", "ReadTransferCount",
                                                      "WriteTransferCount", "OtherTransferCount")]

    class _BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _EXT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BASIC), ("IoInfo", _IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    _K32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _K32.CreateJobObjectW.restype = wintypes.HANDLE
    _K32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _K32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _K32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                               ctypes.c_void_p]
    _K32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _K32.CloseHandle.argtypes = [wintypes.HANDLE]
    _K32.GlobalMemoryStatusEx.argtypes = [ctypes.c_void_p]
    _NTDLL = ctypes.WinDLL("ntdll")
    _NTDLL.NtResumeProcess.argtypes = [wintypes.HANDLE]

CREATE_SUSPENDED = 0x00000004
_JOB_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


def commit_percent() -> float | None:
    """The share of memory in use, or None where it cannot be read."""
    if sys.platform == "win32":
        status = _MEMSTATUS()
        status.dwLength = ctypes.sizeof(status)
        if not _K32.GlobalMemoryStatusEx(ctypes.byref(status)) or not status.ullTotalPageFile:
            return None
        return 100.0 * (status.ullTotalPageFile - status.ullAvailPageFile) / status.ullTotalPageFile
    if sys.platform == "darwin":
        try:
            free = subprocess.run(["sysctl", "-n", "kern.memorystatus_level"], capture_output=True,
                                  text=True, timeout=5, check=True).stdout
            return 100.0 - float(free)
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
    try:
        fields = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        total = float(fields["MemTotal"].split()[0])
        return 100.0 * (total - float(fields["MemAvailable"].split()[0])) / total
    except (OSError, KeyError, ValueError, ZeroDivisionError):
        return None


def physical_gb() -> float | None:
    if sys.platform == "win32":
        status = _MEMSTATUS()
        status.dwLength = ctypes.sizeof(status)
        return status.ullTotalPhys / 1024 ** 3 if _K32.GlobalMemoryStatusEx(ctypes.byref(status)) else None
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3
    except (AttributeError, OSError, ValueError):
        return None


def default_limit_gb(ram_gb: float | None = None) -> float:
    """12 GB, or less on a smaller machine."""
    ram = physical_gb() if ram_gb is None else ram_gb
    return LIMIT_GB_DEFAULT if ram is None else round(min(LIMIT_GB_DEFAULT, ram * LIMIT_SHARE_OF_RAM), 1)


def process_rss_bytes(pid: int) -> int | None:
    """Resident memory of one process on macOS or Linux. Compressed pages are not counted."""
    if sys.platform == "win32":
        return None
    try:
        output = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
        return int(output.stdout.strip()) * 1024 if output.returncode == 0 and output.stdout.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def wait_for_commit(threshold=COMMIT_GATE_DEFAULT, timeout=COMMIT_WAIT_DEFAULT, poll=2.0, read=commit_percent,
                    sleep=time.sleep) -> dict:
    """Wait while memory use is above ``threshold`` percent. ``ok`` False means do not launch."""
    start = time.monotonic()
    first = value = read()
    while value is not None and value > threshold:
        waited = time.monotonic() - start
        if waited >= timeout:
            return {"ok": False, "threshold": threshold, "first": first, "last": value,
                    "waited_s": waited, "timeout_s": timeout}
        sleep(poll)
        value = read()
    return {"ok": True, "threshold": threshold, "first": first, "last": value,
            "waited_s": time.monotonic() - start, "timeout_s": timeout, "available": value is not None}


class ProcessMemoryJob:
    """A Windows job object limiting each member process, closed with the wheel. Inert elsewhere, and says so."""

    def __init__(self, limit_gb: float | None = LIMIT_GB_DEFAULT) -> None:
        self.limit_gb = limit_gb
        self.handle = None
        self.status = "inactive"
        if limit_gb is None or limit_gb <= 0:
            self.status = "disabled"
            return
        if sys.platform != "win32":
            self.status = "unavailable: not Windows"
            return
        job = _K32.CreateJobObjectW(None, None)
        if not job:
            self.status = "failed: CreateJobObject %d" % ctypes.get_last_error()
            return
        info = _EXT()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_PROCESS_MEMORY | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        info.ProcessMemoryLimit = int(limit_gb * 1024 ** 3)
        if not _K32.SetInformationJobObject(job, _JOB_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info),
                                            ctypes.sizeof(info)):
            self.status = "failed: SetInformationJobObject %d" % ctypes.get_last_error()
            _K32.CloseHandle(job)
            return
        self.handle = job
        self.status = "ready"

    def creationflags(self) -> int:
        """Start the child suspended when it will join the job, so it cannot allocate before the limit applies."""
        return CREATE_SUSPENDED if self.handle is not None else 0

    def assign(self, popen) -> str:
        """Put a child started with ``creationflags()`` in the job, then resume it."""
        if self.handle is None:
            return self.status
        if not _K32.AssignProcessToJobObject(self.handle, int(popen._handle)):
            self.status = "failed: AssignProcessToJobObject %d" % ctypes.get_last_error()
        else:
            self.status = "assigned before start: per-process limit %.1f GB" % self.limit_gb
        # subprocess closes the main-thread handle, so resume the whole suspended process.
        result = _NTDLL.NtResumeProcess(int(popen._handle))
        if result != 0:
            self.status += "; NtResumeProcess 0x%08x" % (result & 0xFFFFFFFF)
        return self.status

    def peak_bytes(self) -> int | None:
        if self.handle is None:
            return None
        info = _EXT()
        if not _K32.QueryInformationJobObject(self.handle, _JOB_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info),
                                              ctypes.sizeof(info), None):
            return None
        return int(info.PeakProcessMemoryUsed)

    def close(self) -> None:
        if self.handle is not None:
            _K32.CloseHandle(self.handle)
            self.handle = None
