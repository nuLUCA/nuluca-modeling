"""Loaded inside Blender before any other script. Never imported by the wheel.

An audit hook keeps ordinary Python file access in the modeling work folder and
refuses network and process calls, so a mistaken or misled script fails with a
clear error. This is a guard rail. It sees Python-level calls only: Blender's own
file operators and native code pass beneath it, and a script written to defeat it
can get past it.
"""

import atexit
import os
import sys
import threading
import time

WORK = os.path.realpath(os.environ["NULUCA_BLENDER_WORK_ROOT"])
HOME = os.path.realpath(os.environ["NULUCA_BLENDER_MEMBER_HOME"])
INSTALL = os.path.realpath(os.environ["NULUCA_BLENDER_INSTALL_ROOT"])
READABLE = (WORK, INSTALL, os.path.realpath(sys.prefix), os.path.realpath(sys.base_prefix))
_CASE = os.path.normcase
_REAL = os.path.realpath
_DECODE = os.fsdecode
_IS_DIR = os.path.isdir
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_DEVICES = {_CASE(os.path.realpath(os.devnull))}

sys.dont_write_bytecode = True
# Factory startup skips the user scripts path, so the run's helper module is added here.
sys.path.insert(0, os.path.join(os.getcwd(), "runtime-modules"))


def _inside(path, roots):
    path = _CASE(path)
    return any(path == _CASE(root) or path.startswith(_CASE(root).rstrip(os.sep) + os.sep) for root in roots)


def _resolved(path):
    if isinstance(path, int) or path is None:
        return None
    resolved = _REAL(_DECODE(path))
    return resolved[4:] if resolved.startswith("\\\\?\\") else resolved


def _writable(path):
    path = _resolved(path)
    if path is not None and _CASE(path) not in _DEVICES and not _inside(path, (WORK,)):
        raise PermissionError("nuLUCA Blender guard: writing outside the modeling work folder is refused: " + path)


def _readable(path):
    path = _resolved(path)
    if path is None:
        return
    private = _inside(path, (HOME,)) or path.startswith("/proc/") and not path.startswith("/proc/self")
    # A directory descriptor would let later relative opens skip this check.
    if (private or _IS_DIR(path)) and not _inside(path, READABLE):
        raise PermissionError("nuLUCA Blender guard: reading outside the modeling work folder is refused: " + path)


def _open(args):
    path, mode, flags = args
    if isinstance(mode, str):
        writing = any(mark in mode for mark in "wax+")
    else:
        writing = bool(flags & _WRITE_FLAGS)
    (_writable if writing else _readable)(path)


def _first_writable(args):
    _writable(args[0])


def _two_writable(args):
    _writable(args[0])
    _writable(args[1])


def _first_readable(args):
    _readable(args[0] if args else ".")


def _database(args):
    if args[0] != ":memory:":
        _writable(args[0])


def _refuse(what):
    def refuse(_args):
        raise PermissionError("nuLUCA Blender guard: " + what + " is refused in a modeling script.")
    return refuse


_HANDLERS = {
    "open": _open,
    "os.listdir": _first_readable, "os.scandir": _first_readable, "glob.glob": _first_readable,
    "os.chdir": _first_readable,
    "os.remove": _first_writable, "os.rmdir": _first_writable, "os.mkdir": _first_writable,
    "os.truncate": _first_writable, "os.chmod": _first_writable, "os.chown": _first_writable,
    "os.utime": _first_writable, "os.rename": _two_writable, "os.link": _two_writable,
    "os.symlink": _two_writable,
    "os.chflags": _first_writable, "os.setxattr": _first_writable, "os.removexattr": _first_writable,
    "sqlite3.connect": _database,
}
for _event in ("subprocess.Popen", "os.system", "os.exec", "os.fork", "os.forkpty", "os.posix_spawn", "os.spawn",
               "os.startfile", "pty.spawn"):
    _HANDLERS[_event] = _refuse("starting another program")
for _event in ("socket.__new__", "socket.connect", "socket.bind", "socket.getaddrinfo", "socket.gethostbyname",
               "socket.sendto", "urllib.Request", "http.client.connect", "ftplib.connect", "smtplib.connect",
               "webbrowser.open"):
    _HANDLERS[_event] = _refuse("network access")
for _event in ("os.kill", "os.killpg", "signal.pthread_kill"):
    _HANDLERS[_event] = _refuse("signalling another process")


def _audit(event, args):
    handler = _HANDLERS.get(event)
    if handler is not None:
        handler(args)


sys.addaudithook(_audit)


def _record_peak():
    try:
        import resource
    except ImportError:
        return
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    with open("peak-rss-bytes.txt", "w") as stream:
        stream.write(str(peak if sys.platform == "darwin" else peak * 1024))


atexit.register(_record_peak)


def _leave_with_parent(parent):
    while True:
        time.sleep(2)
        if os.getppid() != parent:
            os._exit(70)


if os.name == "posix":
    threading.Thread(target=_leave_with_parent, args=(int(os.environ["NULUCA_BLENDER_PARENT"]),), daemon=True).start()
