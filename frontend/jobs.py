"""One background job at a time for the webmap: run it, follow it, stop it.

A job is a function taking a reporter, run in a thread: a download, or a whole
pre/post pipeline (download + SNAP). The `Job` object is that reporter — the
interface the features accept, described in `features/snap_gpt/snap_gpt.py`:
`plan`, `step`, `info`, `log`, `run`. What it collects (steps and their
durations, gpt's percentage, rclone's statistics, the end of the log) is what
the page polls on ``/api/job/status``.

The sub-processes it starts (gpt, rclone) are started so that they can be
killed with everything they spawned: on Windows gpt.exe runs the JVM inside
its own process, so killing it is enough; on Linux and macOS gpt is a script
that may start java as a child, so the process gets its own session and the
whole group is signalled. A cancel from the page, and the server stopping
(the page closed, Ctrl+C), both go through `Job.cancel`.

Standard library only, like the server.
"""

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from pathlib import Path

TAIL_LINES = 80          # log lines handed to the page
# gpt draws its progress as "....10%....20%....", without newlines
GPT_PERCENT = re.compile(r"\.\s*(\d{1,3})%")
# Names of the folders a job writes (data/raw/<name>, pre_post/<name>)
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class Cancelled(Exception):
    """The job was cancelled: from the page, or because the server stops."""


def folder_name(value, default):
    """A folder name typed in the page, or ``default`` when empty.

    Only a plain name is accepted — no path, no separator: every job writes
    under the repository's data/ folders.
    """
    value = (value or "").strip() or default
    if not NAME.match(value):
        raise ValueError(
            f"invalid folder name {value!r}: letters, digits, '_', '-', '.' only, no path"
        )
    return value


def _console(text):
    """Echo a log line in the server console, whose code page may not be UTF-8."""
    encoding = sys.stdout.encoding or "ascii"
    print("  | " + text.encode(encoding, "replace").decode(encoding), flush=True)


class Job:
    """A running job and its progress; also the reporter handed to the features."""

    def __init__(self, title, log_path=None):
        self.title = title
        self.status = "running"          # running | done | error | cancelled
        self.started = time.time()
        self.finished = None
        self.steps = []                  # {name, status, started, ended, percent}
        self.data = {}                   # reporter.info(key, value)
        self.download = None             # last rclone statistics
        self.tail = deque(maxlen=TAIL_LINES)
        self.result = None
        self.error = None
        self.log_path = Path(log_path) if log_path else None
        self._log_file = None
        self._proc = None
        self._cancel = threading.Event()
        self._lock = threading.RLock()
        self.thread = None

    # --- reporter interface (see features/snap_gpt/snap_gpt.py) ---------------

    def plan(self, names):
        with self._lock:
            known = {s["name"] for s in self.steps}
            for name in names:
                if name not in known:
                    self.steps.append(self._new_step(name))
                    known.add(name)

    def step(self, name):
        if self._cancel.is_set():
            raise Cancelled()
        with self._lock:
            now = time.time()
            for s in self.steps:
                if s["status"] == "running":
                    s["status"], s["ended"] = "done", now
            current = next((s for s in self.steps if s["name"] == name), None)
            if current is None:
                current = self._new_step(name)
                self.steps.append(current)
            current.update(status="running", started=now, ended=None, percent=None)
        self.log(f"== {name}")

    def info(self, key, value):
        with self._lock:
            self.data[key] = value

    def log(self, text):
        text = str(text).rstrip()
        if not text:
            return
        with self._lock:
            self.tail.append(text)
            if self._log_file:
                self._log_file.write(text + "\n")
                self._log_file.flush()
        _console(text)

    def run(self, command, cwd=None, kind=None):
        """Run a sub-process, following its output; return its exit code."""
        if self._cancel.is_set():
            raise Cancelled()
        options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, cwd=cwd)
        if os.name == "posix":
            options["start_new_session"] = True  # its own process group, see the module docstring
        proc = subprocess.Popen([str(c) for c in command], **options)
        with self._lock:
            self._proc = proc
        if self._cancel.is_set():  # cancelled between the check and Popen
            self._kill(proc)
        try:
            if kind == "rclone":
                self._read_rclone(proc)
            else:
                self._read_stream(proc)
            code = proc.wait()
        finally:
            with self._lock:
                self._proc = None
        if self._cancel.is_set():
            raise Cancelled()
        if code != 0:
            self.log(f"{Path(str(command[0])).name} exited with code {code}")
        return code

    # --- output readers --------------------------------------------------------

    def _read_stream(self, proc):
        """Log the output line by line; the gpt progress comes without newlines,
        so the stream is read by chunks and the percentages picked on the fly."""
        pending = ""
        while True:
            chunk = proc.stdout.read1(65536)
            if not chunk:
                break
            # The dots and the number come in separate chunks ("....", "10%"):
            # look in the line being built, not in the chunk alone
            *lines, pending = re.split(r"\r\n|\r|\n", pending + chunk.decode("utf-8", "replace"))
            percents = GPT_PERCENT.findall(pending)
            if percents:
                self._set_percent(int(percents[-1]))
            for line in lines:
                self.log(line)
        self.log(pending)

    def _read_rclone(self, proc):
        """rclone --use-json-log: one JSON record per line, statistics included."""
        last_summary = None
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                self.log(line)
                continue
            stats = record.get("stats")
            if stats is None:
                self.log(f"{record.get('level', '')}: {record.get('msg', '')}".strip(": "))
                continue
            with self._lock:
                self.download = {
                    "bytes": stats.get("bytes", 0),
                    "total_bytes": stats.get("totalBytes", 0),
                    "speed": stats.get("speed", 0),
                    "eta": stats.get("eta"),
                    "transfers": stats.get("transfers", 0),
                    "total_transfers": stats.get("totalTransfers", 0),
                    "errors": stats.get("errors", 0),
                }
            total = stats.get("totalBytes") or 0
            if total:
                self._set_percent(int(100 * stats.get("bytes", 0) / total))
            last_summary = record.get("msg")
        # The statistics are not logged one per second: only the last one
        if last_summary:
            for text in last_summary.strip().splitlines():
                self.log(text)

    def _set_percent(self, value):
        with self._lock:
            for s in self.steps:
                if s["status"] == "running":
                    s["percent"] = max(0, min(100, value))

    # --- control ---------------------------------------------------------------

    def cancel(self):
        self._cancel.set()
        with self._lock:
            proc = self._proc
        if proc is not None:
            self._kill(proc)

    @staticmethod
    def _kill(proc):
        if proc.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass  # already gone

    def execute(self, fn):
        """Body of the job thread: run fn(self), record how it ended."""
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = open(self.log_path, "w", encoding="utf-8")
        self.log(f"{self.title} - started {time.strftime('%Y-%m-%d %H:%M:%S')}")
        outcome, error = "done", None
        try:
            self.result = fn(self)
        except Cancelled:
            outcome = "cancelled"
        except Exception as exc:  # noqa: BLE001 — anything must reach the page
            outcome, error = "error", f"{type(exc).__name__}: {exc}"
            for line in traceback.format_exc().splitlines():
                self.log(line)
        with self._lock:
            now = time.time()
            for s in self.steps:
                if s["status"] == "running":
                    s["status"] = {"done": "done", "cancelled": "cancelled"}.get(outcome, "error")
                    s["ended"] = now
            self.status, self.error, self.finished = outcome, error, now
        self.log(f"{self.title} - {outcome}" + (f": {error}" if error else ""))
        with self._lock:
            if self._log_file:
                self._log_file.close()
                self._log_file = None

    # --- what the page gets ------------------------------------------------------

    @staticmethod
    def _new_step(name):
        return {"name": name, "status": "pending", "started": None, "ended": None, "percent": None}

    def snapshot(self):
        with self._lock:
            now = time.time()
            steps = []
            for s in self.steps:
                duration = None
                if s["started"] is not None:
                    duration = (s["ended"] or now) - s["started"]
                steps.append({"name": s["name"], "status": s["status"],
                              "percent": s["percent"], "duration": duration})
            return {
                "title": self.title,
                "status": self.status,
                "elapsed": (self.finished or now) - self.started,
                "steps": steps,
                "info": self.data,
                "download": self.download,
                "tail": list(self.tail),
                "result": self.result,
                "error": self.error,
                "log_path": str(self.log_path) if self.log_path else None,
            }


# --- The one job of the server ------------------------------------------------------

_current = None
_current_lock = threading.Lock()


def start(title, fn, log_path=None):
    """Start ``fn(reporter)`` in the background; refused while another job runs."""
    global _current
    with _current_lock:
        if _current is not None and _current.status == "running":
            raise RuntimeError(f"a job is already running: {_current.title}")
        job = Job(title, log_path)
        job.thread = threading.Thread(target=job.execute, args=(fn,), daemon=True)
        _current = job
    print(f"  job: {title}", flush=True)
    job.thread.start()
    return job.snapshot()


def shutdown(timeout=15):
    """Stop the running job, if any, and wait for its thread: the server is closing."""
    job = _current
    if job is not None and job.status == "running":
        print("  stopping the running job...", flush=True)
        job.cancel()
        job.thread.join(timeout)


def api_status(_body, _config):
    job = _current
    return {"status": "idle"} if job is None else job.snapshot()


def api_cancel(_body, _config):
    job = _current
    if job is None or job.status != "running":
        raise RuntimeError("no job running")
    job.cancel()
    return {"ok": True}


def api_clear(_body, _config):
    """Forget a finished job, so a reloaded page does not show it again."""
    global _current
    with _current_lock:
        if _current is not None and _current.status != "running":
            _current = None
    return {"ok": True}


ROUTES = {
    "GET": {"/api/job/status": api_status},
    "POST": {"/api/job/cancel": api_cancel, "/api/job/clear": api_clear},
}
