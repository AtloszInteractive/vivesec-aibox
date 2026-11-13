"""The box clock as the administrator sees it (E27 S7), pure stdlib.

TOTP codes only work while the box's clock is right (one 30-second step of
tolerance either way). The administration page shows two things:

* whether the kernel clock is disciplined by a time service (chrony,
  systemd-timesyncd): `adjtimex` with no mode bits only reads the state, needs
  no privilege and works inside a container. A time service that has not
  synchronised yet leaves the kernel in TIME_ERROR / STA_UNSYNC.
* the box's time itself, which the browser compares with its own clock.

Anything this module cannot read is "unknown", never "synchronized".
"""
import ctypes
import ctypes.util
import sys

TIME_ERROR = 5          # clock state: not synchronised
STA_UNSYNC = 0x0040     # status bit: no time service has synchronised the clock
MAX_ERROR_US = 16000000  # the kernel's ceiling for maxerror (16 s): "no idea"


class _Timex(ctypes.Structure):
    # struct timex of <sys/timex.h>; ctypes adds the same padding as the C ABI.
    _fields_ = [("modes", ctypes.c_uint), ("offset", ctypes.c_long), ("freq", ctypes.c_long),
                ("maxerror", ctypes.c_long), ("esterror", ctypes.c_long),
                ("status", ctypes.c_int), ("constant", ctypes.c_long),
                ("precision", ctypes.c_long), ("tolerance", ctypes.c_long),
                ("time_sec", ctypes.c_long), ("time_usec", ctypes.c_long),
                ("tick", ctypes.c_long), ("ppsfreq", ctypes.c_long), ("jitter", ctypes.c_long),
                ("shift", ctypes.c_int), ("stabil", ctypes.c_long), ("jitcnt", ctypes.c_long),
                ("calcnt", ctypes.c_long), ("errcnt", ctypes.c_long), ("stbcnt", ctypes.c_long),
                ("tai", ctypes.c_int), ("_reserved", ctypes.c_int * 11)]


def _libc_adjtimex():
    if not sys.platform.startswith("linux"):
        return None
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or None, use_errno=True)
    except OSError:
        return None
    function = getattr(libc, "adjtimex", None)
    if function is None:
        return None
    function.argtypes = [ctypes.POINTER(_Timex)]
    function.restype = ctypes.c_int
    return function


def interpret(state, status, max_error_us, est_error_us):
    """The clock report for one adjtimex answer."""
    if state < 0:
        return unknown("adjtimex refused")
    synchronized = state != TIME_ERROR and not status & STA_UNSYNC
    report = {"state": "synchronized" if synchronized else "unsynchronized"}
    if 0 <= max_error_us < MAX_ERROR_US:
        report["max_error_ms"] = round(max_error_us / 1000.0, 1)
    if 0 <= est_error_us < MAX_ERROR_US:
        report["est_error_ms"] = round(est_error_us / 1000.0, 1)
    return report


def unknown(reason):
    return {"state": "unknown", "reason": reason}


def kernel_sync(adjtimex=None):
    """{"state": "synchronized" | "unsynchronized" | "unknown", ...}."""
    function = adjtimex if adjtimex is not None else _libc_adjtimex()
    if function is None:
        return unknown("not available on this system")
    buffer = _Timex()  # modes = 0: read only
    try:
        state = function(ctypes.byref(buffer))
    except (OSError, ctypes.ArgumentError) as e:
        return unknown(str(e))
    return interpret(state, buffer.status, buffer.maxerror, buffer.esterror)
