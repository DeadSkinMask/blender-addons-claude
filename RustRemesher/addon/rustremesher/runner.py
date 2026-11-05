# SPDX-License-Identifier: GPL-3.0-or-later
"""Runs the engine as a subprocess (protocol in ../../FORMAT.md): the settings file goes in with `-s`, the engine
writes a progress file the addon polls, and the result is a mesh file. No bpy here."""

import os
import shutil
import subprocess
import sys
import tempfile

from . import exchange

EXE = "rustremesher.exe" if sys.platform == "win32" else "rustremesher"


class EngineError(RuntimeError):
    pass


def find_engine(configured=""):
    """Path of the engine: the configured one, else `bin/` inside the addon, else the PATH. Raises EngineError."""
    if configured:
        path = os.path.abspath(os.path.expanduser(configured))
        if os.path.isdir(path):
            path = os.path.join(path, EXE)
        if not os.path.isfile(path):
            raise EngineError("No se encuentra el engine en '{}'. Configura la ruta en las preferencias del addon.".format(configured))
        if not os.access(path, os.X_OK):
            raise EngineError("El engine '{}' no es ejecutable.".format(path))
        return path
    bundled = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", EXE)
    if os.path.isfile(bundled):
        return bundled
    found = shutil.which(EXE)
    if found:
        return found
    raise EngineError("No hay engine configurado. Indica la ruta de {} en las preferencias del addon.".format(EXE))


def engine_version(path):
    out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20)
    if out.returncode != 0:
        raise EngineError("El engine no responde a --version: {}".format(out.stderr.strip() or out.returncode))
    return out.stdout.strip()


def settings_text(values):
    """`Key=value` lines; strings (paths) are quoted, booleans are 0/1."""
    lines = []
    for key, value in values.items():
        if isinstance(value, bool):
            value = int(value)
        elif isinstance(value, str):
            value = '"{}"'.format(value.replace("\\", "/"))
        lines.append("{}={}".format(key, value))
    return "\n".join(lines) + "\n"


def read_progress(path):
    """(value, text) from the progress file, or (None, "") while it does not exist yet."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return None, ""
    if not lines:
        return None, ""
    try:
        value = float(lines[0])
    except ValueError:
        return None, ""
    return value, (lines[1] if len(lines) > 1 else "")


class EngineJob:
    """One run: writes the input, starts the engine, `poll()` until it is not running, then `result()`."""

    def __init__(self, engine, mesh_bytes, settings):
        self.dir = tempfile.mkdtemp(prefix="rustremesher_")
        self.input = os.path.join(self.dir, "input.rrm")
        self.output = os.path.join(self.dir, "output.rrm")
        self.progress = os.path.join(self.dir, "progress.txt")
        self.log = os.path.join(self.dir, "engine.log")
        self.proc = None
        try:
            with open(self.input, "wb") as f:
                f.write(mesh_bytes)
            values = {"FileIn": self.input, "FileOut": self.output, "ProgressFile": self.progress}
            values.update(settings)
            settings_path = os.path.join(self.dir, "settings.txt")
            with open(settings_path, "w", encoding="utf-8") as f:
                f.write(settings_text(values))
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            with open(self.log, "wb") as log:
                self.proc = subprocess.Popen([engine, "-s", settings_path], stdout=log, stderr=log, creationflags=flags)
        except Exception:
            self.cleanup()
            raise

    def poll(self):
        """('running', value, text), ('done', 1.0, text) or ('error', 0.0, message)."""
        value, text = read_progress(self.progress)
        code = self.proc.poll()
        if code is None:
            return "running", max(0.0, min(value or 0.0, 1.0)), text
        if code == 0 and os.path.isfile(self.output):
            return "done", 1.0, text
        message = text if value is not None and value < 0 and text else self._log_tail()
        return "error", 0.0, "El engine terminó con código {}: {}".format(code, message or "sin mensaje")

    def _log_tail(self):
        try:
            with open(self.log, encoding="utf-8", errors="replace") as f:
                return f.read().strip().splitlines()[-1]
        except (OSError, IndexError):
            return ""

    def result(self):
        with open(self.output, "rb") as f:
            return exchange.decode_mesh(f.read())

    def cancel(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.cleanup()

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)
