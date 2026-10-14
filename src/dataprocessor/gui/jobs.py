import re
import sys
import threading
import time
import traceback
import uuid

# SETUP ---------------------------------------------------------------------------
# One job runs at a time. The GUI polls JobManager.poll() for new feed rows,
# log lines and progress; runners report through the JobContext they are given.

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
MAX_LOG = 5000
# ---------------------------------------------------------------------------------


class Cancelled(Exception):
    pass


class JobContext:
    """Handed to a runner. Everything here is thread safe."""

    def __init__(self, job):
        self._job = job
        self.cancel_event = job.cancel_event

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def check_cancel(self):
        if self.cancel_event.is_set():
            raise Cancelled()

    def set_total(self, total, unit="rows"):
        with self._job.lock:
            self._job.total = total
            self._job.unit = unit

    def set_done(self, done):
        with self._job.lock:
            self._job.done = done

    def advance(self, n=1):
        with self._job.lock:
            self._job.done += n

    def phase(self, text, log=True):
        with self._job.lock:
            self._job.phase = text
        if log:
            self.log(f"── {text}", "phase")

    def row(self, data: dict):
        with self._job.lock:
            self._job.rows.append(data)

    def active(self, items: list):
        with self._job.lock:
            self._job.active = items

    def log(self, msg, level="info"):
        self._job.add_log(msg, level)

    def on_line(self, fn):
        """Register a parser for lines the logic modules print to stdout."""
        self._job.line_hook = fn


class Job:
    def __init__(self, tool, params):
        self.id = uuid.uuid4().hex[:8]
        self.tool = tool
        self.params = params
        self.state = "running"
        self.phase = "Starting"
        self.started = time.time()
        self.finished = None
        self.total = 0
        self.done = 0
        self.unit = "rows"
        self.rows = []
        self.logs = []
        self.log_offset = 0
        self.active = []
        self.summary = None
        self.error = None
        self.cancel_event = threading.Event()
        self.lock = threading.Lock()
        self.line_hook = None

    def add_log(self, msg, level="info"):
        msg = ANSI.sub("", str(msg)).rstrip()
        if not msg.strip():
            return
        with self.lock:
            self.logs.append({"t": time.strftime("%H:%M:%S"), "level": level, "msg": msg})
            if len(self.logs) > MAX_LOG:
                drop = len(self.logs) - MAX_LOG
                del self.logs[:drop]
                self.log_offset += drop


class JobManager:
    def __init__(self):
        self.jobs = {}
        self.current = None
        self._lock = threading.Lock()

    def start(self, tool, params, runner):
        with self._lock:
            if self.current and self.current.state == "running":
                raise RuntimeError(f"A {self.current.tool} job is already running. Stop it or wait for it to finish.")
            job = Job(tool, params)
            self.jobs[job.id] = job
            self.current = job

        def target():
            ctx = JobContext(job)
            try:
                job.summary = runner(ctx, params)
                job.state = "cancelled" if job.cancel_event.is_set() else "done"
            except Cancelled:
                job.state = "cancelled"
                job.add_log("Stopped by user. Rows already written are kept.", "warn")
            except ValueError as e:
                # Input problems raised by the runners: show the message, no traceback.
                job.state = "error"
                job.error = str(e)
                job.add_log(job.error, "error")
            except Exception as e:
                job.state = "error"
                job.error = f"{type(e).__name__}: {e}"
                job.add_log(job.error, "error")
                traceback.print_exc(file=sys.__stderr__)
            finally:
                job.finished = time.time()

        threading.Thread(target=target, daemon=True, name=f"job-{job.id}").start()
        return job.id

    def stop(self, job_id):
        job = self.jobs.get(job_id)
        if job and job.state == "running":
            job.cancel_event.set()
            job.add_log("Stop requested. Finishing the batch in flight...", "warn")
            return True
        return False

    def poll(self, job_id, row_cursor=0, log_cursor=0):
        job = self.jobs.get(job_id)
        if job is None:
            return None
        with job.lock:
            start = max(0, log_cursor - job.log_offset)
            end = time.time() if job.finished is None else job.finished
            return {
                "id": job.id,
                "tool": job.tool,
                "state": job.state,
                "phase": job.phase,
                "total": job.total,
                "done": job.done,
                "unit": job.unit,
                "elapsed": end - job.started,
                "rows": job.rows[row_cursor:],
                "row_cursor": len(job.rows),
                "logs": job.logs[start:],
                "log_cursor": job.log_offset + len(job.logs),
                "active": job.active,
                "summary": job.summary,
                "error": job.error,
                "cancel_requested": job.cancel_event.is_set(),
            }

    def capture(self, text):
        job = self.current
        if job is None or job.state != "running":
            return
        for line in re.split(r"[\r\n]+", text):
            if not line.strip():
                continue
            job.add_log(line, "out")
            if job.line_hook:
                try:
                    job.line_hook(ANSI.sub("", line))
                except Exception:
                    pass


class StdoutTee:
    """Mirrors stdout to the terminal and into the running job's log, so the
    print()/console.print() calls inside the logic modules show up in the GUI."""

    def __init__(self, original, manager: JobManager):
        self._original = original
        self._manager = manager
        self._buf = ""
        self._lock = threading.Lock()

    def write(self, text):
        try:
            self._original.write(text)
        except Exception:
            pass
        with self._lock:
            self._buf += text
            if "\n" not in self._buf and "\r" not in self._buf:
                return len(text)
            *lines, self._buf = re.split(r"(?<=[\r\n])", self._buf)
        self._manager.capture("".join(lines))
        return len(text)

    def flush(self):
        try:
            self._original.flush()
        except Exception:
            pass

    def isatty(self):
        return False

    def fileno(self):
        return self._original.fileno()

    @property
    def encoding(self):
        return getattr(self._original, "encoding", "utf-8")
