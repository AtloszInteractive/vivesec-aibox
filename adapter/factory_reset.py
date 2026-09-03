"""Physical factory-reset pin monitor for the AIBox (J6, spec sec 1).

The ViVeSecBox team confirmed factory reset is a PHYSICAL action, NOT an API
endpoint (the controlling ViVeSecBox may be unreachable/faulty when the operator
needs to wipe and re-pair the box). This monitors a pin that, when held for
``hold_seconds`` (router-style 5+ s), fires a single ``on_reset`` callback which
de-provisions the box (wipe PKI, clear the index/mirror/sessions, lock storage).

The pin reader is injectable so the trigger source is pluggable and the state
machine is unit-testable without hardware:

    ADAPTER_RESET_PIN_MODE :
        off  (default) : no monitor.
        gpio           : read a Linux sysfs GPIO value file; pressed when the
                         value equals ADAPTER_RESET_GPIO_ACTIVE (default "0",
                         i.e. active-low with an internal pull-up).
        file           : a sentinel file exists -> pressed (ops/testing without
                         GPIO; hold == keep the file present for hold_seconds).

    ADAPTER_RESET_GPIO_PATH   : sysfs value path (e.g. /sys/class/gpio/gpio18/value)
    ADAPTER_RESET_GPIO_ACTIVE : active level char (default "0")
    ADAPTER_RESET_PIN_FILE    : sentinel path for 'file' mode
    ADAPTER_RESET_HOLD_SECONDS: continuous-hold threshold (default 5)

A press must be RELEASED before it can fire again, so a stuck pin reboots into a
single reset, not a loop.

Pure stdlib, Py3.8+.
"""
import os
import sys
import threading
import time

_TRUE = ("1", "true", "yes", "on")


def gpio_reader(path, active="0"):
    """Pin reader backed by a Linux sysfs GPIO value file."""
    def read():
        try:
            with open(path) as f:
                return f.read().strip() == active
        except OSError:
            return False
    return read


def file_reader(path):
    """Pin reader backed by a sentinel file's existence."""
    def read():
        return bool(path) and os.path.exists(path)
    return read


class FactoryResetMonitor:
    def __init__(self, pin_reader, on_reset, hold_seconds=5.0, poll_interval=0.5):
        self.pin_reader = pin_reader
        self.on_reset = on_reset
        self.hold_seconds = float(hold_seconds)
        self.poll_interval = float(poll_interval)
        self._stop = threading.Event()
        self._press_started = None
        self._fired_this_press = False

    def stop(self):
        self._stop.set()

    def poll_once(self, now=None):
        """Advance the state machine by one observation. Returns True iff this
        observation fired the reset. Exposed for deterministic unit testing."""
        now = time.time() if now is None else now
        pressed = bool(self.pin_reader())
        if not pressed:
            self._press_started = None
            self._fired_this_press = False
            return False
        if self._press_started is None:
            self._press_started = now
        if (not self._fired_this_press
                and now - self._press_started >= self.hold_seconds):
            self._fired_this_press = True
            try:
                self.on_reset()
            except Exception as e:  # noqa: BLE001
                sys.stderr.write("[adapter] factory reset callback failed: %s\n" % e)
            return True
        return False

    def run(self):
        """Poll the pin until stop(); fire on_reset once per sustained hold.
        Intended to run on a daemon thread."""
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.poll_interval)


def make_reader_from_env(env=None):
    """Return (mode, pin_reader) or (mode, None) if disabled/misconfigured."""
    env = env or os.environ
    mode = (env.get("ADAPTER_RESET_PIN_MODE", "off") or "off").strip().lower()
    if mode == "gpio":
        path = env.get("ADAPTER_RESET_GPIO_PATH", "")
        if not path:
            return mode, None
        return mode, gpio_reader(path, env.get("ADAPTER_RESET_GPIO_ACTIVE", "0"))
    if mode == "file":
        path = env.get("ADAPTER_RESET_PIN_FILE", "")
        if not path:
            return mode, None
        return mode, file_reader(path)
    return mode, None


def from_env(on_reset, env=None):
    """Build a FactoryResetMonitor from the environment, or None if disabled."""
    env = env or os.environ
    mode, reader = make_reader_from_env(env)
    if reader is None:
        return None
    hold = float(env.get("ADAPTER_RESET_HOLD_SECONDS", "5") or 5)
    return FactoryResetMonitor(reader, on_reset, hold_seconds=hold)
