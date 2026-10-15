"""One-process, loopback-only web launcher."""

import threading
import webbrowser

import uvicorn

from jobradar.config import Settings
from jobradar.web.app import create_app


def run_web(settings: Settings, *, port: int = 8765, open_browser: bool = False) -> None:
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings), host="127.0.0.1", port=port, workers=1, proxy_headers=False
        )
    )
    finished = threading.Event()
    if open_browser:

        def open_when_ready() -> None:
            while not finished.wait(0.1):
                if server.started:
                    webbrowser.open(f"http://127.0.0.1:{port}")
                    return

        threading.Thread(target=open_when_ready, daemon=True, name="jobradar-browser").start()
    try:
        server.run()
    finally:
        finished.set()
