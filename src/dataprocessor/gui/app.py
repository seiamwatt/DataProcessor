import os
import sys

import webview

from dataprocessor.config import load_env
from dataprocessor.gui.api import Api
from dataprocessor.gui.jobs import JobManager, StdoutTee

# SETUP ---------------------------------------------------------------------------
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
# ---------------------------------------------------------------------------------


def main():
    load_env()
    jobs = JobManager()
    sys.stdout = StdoutTee(sys.stdout, jobs)

    api = Api(jobs)
    window = webview.create_window(
        "DataProcessor",
        url=os.path.join(WEB_DIR, "index.html"),
        js_api=api,
        width=1280,
        height=820,
        min_size=(900, 600),
        background_color="#300A24",
        text_select=True,
    )
    api._set_window(window)
    webview.start(http_server=True, debug="--debug" in sys.argv)


if __name__ == "__main__":
    main()

# ---------------------------------------------------------------------------------
# run locally:            uv run dataprocessor-gui   (add --debug for the web inspector)
