# SPDX-License-Identifier: GPL-3.0-or-later
"""Runs UnWrapConsole3.exe as a subprocess: natively on Windows, through Wine on Linux/macOS. No bpy here.
The tool exits with code 0 even when it fails (and with 1 on a few successful runs), so success means the
output file exists; the error text is taken from its console output."""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

EXE_NAMES = ("UnWrapConsole3.exe", "UnwrapConsole3.exe")
IS_WINDOWS = sys.platform == "win32"


class EngineError(RuntimeError):
    pass


def find_exe(configured=""):
    """Path of UnWrapConsole3.exe: the configured file (or folder containing it), else `bin/` in the addon, else PATH."""
    candidates = []
    if configured:
        path = os.path.abspath(os.path.expanduser(configured))
        if os.path.isdir(path):
            found = _in_dir(path)
            if found:
                return found
            raise EngineError("UnWrapConsole3.exe not found in '{}'.".format(configured))
        if not os.path.isfile(path):
            raise EngineError("Unwrapper not found at '{}'. Set its path in the add-on preferences.".format(configured))
        return path
    found = _in_dir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin"))
    if found:
        return found
    for name in EXE_NAMES:
        found = shutil.which(name)
        if found:
            return found
    raise EngineError("No unwrapper configured. Set the path of UnWrapConsole3.exe in the add-on preferences.")


def _in_dir(folder):
    if not os.path.isdir(folder):
        return None
    wanted = {n.lower() for n in EXE_NAMES}
    for name in os.listdir(folder):
        if name.lower() in wanted:
            return os.path.join(folder, name)
    return None


def find_wine(configured=""):
    """Wine launcher to use, or None on Windows."""
    if IS_WINDOWS:
        return None
    if configured:
        path = os.path.abspath(os.path.expanduser(configured))
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
        raise EngineError("Wine not found at '{}'.".format(configured))
    for name in ("wine64", "wine"):
        found = shutil.which(name)
        if found:
            return found
    raise EngineError("Wine is needed to run the unwrapper on this system (install it, e.g. `sudo apt install wine64`) "
                      "or set its path in the add-on preferences.")


def settings_args(values):
    """Command line options: booleans become TRUE/FALSE, numbers are passed as they are."""
    args = []
    for key, value in values.items():
        args.append("-" + key)
        if isinstance(value, bool):
            args.append("TRUE" if value else "FALSE")
        elif isinstance(value, float):
            args.append("{:.6f}".format(value))
        else:
            args.append(str(value))
    return args


class EngineJob:
    """One run. The input is written by the caller to `input_path`; `poll()` until it is not running."""

    def __init__(self, exe, wine, settings, timeout=0):
        self.dir = tempfile.mkdtemp(prefix="mof_bridge_")
        self.input_path = os.path.join(self.dir, "input.obj")
        self.output_path = os.path.join(self.dir, "output.obj")
        self.log_path = os.path.join(self.dir, "unwrapper.log")
        self.exe, self.wine, self.settings, self.timeout = exe, wine, settings, timeout
        self.proc = None
        self.started = None

    def start(self):
        # relative file names with the job folder as cwd: no Windows/Wine path translation needed
        cmd = [self.exe, "input.obj", "output.obj"] + settings_args(self.settings)
        if self.wine:
            cmd = [self.wine] + cmd
        env = dict(os.environ)
        if self.wine:
            env.setdefault("WINEDEBUG", "-all")
            env.setdefault("WINEDLLOVERRIDES", "mscoree,mshtml=")  # no Mono/Gecko installer prompts
        kwargs = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if IS_WINDOWS else {"start_new_session": True}
        self.started = time.monotonic()
        try:
            with open(self.log_path, "wb") as log:
                self.proc = subprocess.Popen(cmd, cwd=self.dir, env=env, stdout=log, stderr=log, **kwargs)
        except OSError as e:
            self.cleanup()
            raise EngineError("Could not start the unwrapper: {}".format(e))

    def elapsed(self):
        return time.monotonic() - self.started if self.started else 0.0

    def poll(self):
        """('running', None), ('done', None) or ('error', message)."""
        code = self.proc.poll()
        if code is None:
            if self.timeout and self.elapsed() > self.timeout:
                self.cancel()
                return "error", ("The unwrapper did not finish in {} s and was stopped. It may have crashed (Wine can leave "
                                 "an error dialog open) on degenerate geometry such as zero-area or non-planar n-gons."
                                 .format(self.timeout))
            return "running", None
        if os.path.isfile(self.output_path) and os.path.getsize(self.output_path) > 0:
            return "done", None
        return "error", "The unwrapper produced no output (exit code {}): {}".format(code, self._error_text() or "no message")

    def _error_text(self):
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as f:
                lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
        except OSError:
            return ""
        errors = [ln for ln in lines if "rror" in ln]
        return (errors or lines or [""])[-1]

    def cancel(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                if IS_WINDOWS:
                    self.proc.terminate()
                else:
                    os.killpg(self.proc.pid, signal.SIGTERM)  # wine and the exe it spawned
            except OSError:
                pass
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.cleanup()

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)
