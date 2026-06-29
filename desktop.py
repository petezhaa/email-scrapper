"""Desktop app entry point: runs the local server and shows it in a native window.

This is what gets packaged into the single-file .exe / .app (see build_exe.py).
Run directly during development with:  python desktop.py
"""
from __future__ import annotations

import socket
import threading
import time

import webview

from src.app import app


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_until_up(port: int, timeout: float = 15.0) -> None:
    """Block until the server accepts connections (so the window isn't blank)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.25)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)


def main() -> None:
    port = _free_port()

    def serve() -> None:
        # threaded=True so the UI stays responsive during long requests.
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)

    threading.Thread(target=serve, daemon=True).start()
    _wait_until_up(port)

    webview.create_window(
        "Research Outreach",
        f"http://127.0.0.1:{port}",
        width=1040,
        height=860,
        min_size=(760, 600),
    )
    webview.start()


if __name__ == "__main__":
    main()
