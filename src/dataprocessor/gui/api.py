import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import webview
from dotenv import set_key

from dataprocessor.config import load_env
from dataprocessor.gui import runners
from dataprocessor.gui.jobs import JobManager

# SETUP ---------------------------------------------------------------------------
HOME_DIR = os.path.join(os.path.expanduser("~"), ".dataprocessor")
HOME_ENV = os.path.join(HOME_DIR, ".env")
STATE_FILE = os.path.join(HOME_DIR, "gui_state.json")
KEY_NAMES = ["DeepSeek_key", "GPT_key", "Gemini_key"]
# ---------------------------------------------------------------------------------


def mask(value):
    if not value:
        return ""
    return value[:4] + "•" * 8 + value[-4:] if len(value) > 10 else "•" * len(value)


def json_safe(value):
    """pywebview serialises return values as JSON; numpy/pandas scalars aren't."""
    return json.loads(json.dumps(value, default=str))


class Api:
    """Every public method here is callable from JS as window.pywebview.api.<name>().
    Attributes start with an underscore so pywebview doesn't try to expose them."""

    def __init__(self, jobs: JobManager):
        self._jobs = jobs
        self._window = None
        self._started = time.time()

    def _set_window(self, window):
        self._window = window

    # INFO ----------------------------------------------------------------------
    def get_info(self):
        load_env()
        try:
            version = importlib.metadata.version("dataprocessor")
        except importlib.metadata.PackageNotFoundError:
            version = os.getenv("version") or "dev"
        return {
            "version": version,
            "started": self._started,
            "keys": {k: bool(os.getenv(k)) for k in KEY_NAMES},
            "states": runners.STATES,
            "rpm_choices": [[str(v), k] for k, v in runners.RPM_CHOICES.items()],
            "not_cancellable": sorted(runners.NOT_CANCELLABLE),
            "platform": platform.system(),
            "cwd": os.getcwd(),
            "env_file": HOME_ENV,
        }

    # FILES ---------------------------------------------------------------------
    def pick(self, kind, start_dir="", filename=""):
        """kind: 'csv' | 'save_csv' | 'folder'"""
        start_dir = os.path.expanduser(start_dir or "")
        if start_dir and not os.path.isdir(start_dir):
            start_dir = os.path.dirname(start_dir)
        if kind == "folder":
            result = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=start_dir)
        elif kind == "save_csv":
            result = self._window.create_file_dialog(
                webview.FileDialog.SAVE, directory=start_dir,
                save_filename=filename or "output.csv")
        else:
            result = self._window.create_file_dialog(
                webview.FileDialog.OPEN, directory=start_dir,
                file_types=("CSV files (*.csv)", "All files (*.*)"))
        if not result:
            return None
        return result if isinstance(result, str) else result[0]

    def inspect_csv(self, tool, path):
        try:
            return json_safe({"ok": True, **runners.inspect_csv(tool, path)})
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def preview_csv(self, path, n=200):
        path = runners.clean_path(path)
        if not os.path.isfile(path):
            return {"ok": False, "error": f"File not found: {path}"}
        try:
            df = pd.read_csv(path, nrows=n)
            with open(path, "rb") as f:
                total = max(0, sum(1 for _ in f) - 1)
            df = df.fillna("")
            return json_safe({
                "ok": True,
                "path": path,
                "columns": [str(c) for c in df.columns],
                "rows": df.astype(str).values.tolist(),
                "total": total,
                "size": os.path.getsize(path),
            })
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def reveal(self, path):
        path = runners.clean_path(path)
        if not path or not os.path.exists(path):
            path = os.path.dirname(path)
            if not os.path.exists(path):
                return False
        system = platform.system()
        if system == "Darwin":
            subprocess.Popen(["open", "-R", path] if os.path.isfile(path) else ["open", path])
        elif system == "Windows":
            subprocess.Popen(["explorer", "/select,", path] if os.path.isfile(path) else ["explorer", path])
        else:
            subprocess.Popen(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])
        return True

    # JOBS ----------------------------------------------------------------------
    def start(self, tool, params):
        runner = runners.RUNNERS.get(tool)
        if runner is None:
            return {"ok": False, "error": f"Unknown tool: {tool}"}
        try:
            job_id = self._jobs.start(tool, params, runner)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "job_id": job_id, "cancellable": tool not in runners.NOT_CANCELLABLE}

    def poll(self, job_id, row_cursor=0, log_cursor=0):
        return json_safe(self._jobs.poll(job_id, row_cursor, log_cursor))

    def stop(self, job_id):
        return self._jobs.stop(job_id)

    # SETTINGS ------------------------------------------------------------------
    def get_settings(self):
        load_env()
        return {
            "env_file": HOME_ENV,
            "keys": [{"name": k, "set": bool(os.getenv(k)), "masked": mask(os.getenv(k))} for k in KEY_NAMES],
        }

    def save_key(self, name, value):
        if name not in KEY_NAMES:
            return {"ok": False, "error": f"Unknown key {name}"}
        value = (value or "").strip()
        if not value:
            return {"ok": False, "error": "Value is empty."}
        os.makedirs(HOME_DIR, exist_ok=True)
        if not os.path.exists(HOME_ENV):
            open(HOME_ENV, "a").close()
        set_key(HOME_ENV, name, value, quote_mode="never")
        os.environ[name] = value
        return {"ok": True, "masked": mask(value)}

    def test_connections(self):
        load_env()
        from dataprocessor.main_section import main_util
        tests = {
            "DeepSeek_key": main_util.DeepSeek_connect_test,
            "GPT_key": main_util.GTP_connect_test,
            "Gemini_key": main_util.Gemini_connect_test,
        }
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = {k: ex.submit(fn) for k, fn in tests.items()}
            return {k: bool(f.result()) for k, f in futures.items()}

    # UI STATE ------------------------------------------------------------------
    def load_state(self):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            return {}

    def save_state(self, state):
        try:
            os.makedirs(HOME_DIR, exist_ok=True)
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
            return True
        except Exception:
            return False

    def quit(self):
        self._window.destroy()
