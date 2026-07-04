"""Launch the new Next.js + shadcn frontend together with the Python pipeline API.

    python run_web.py

Starts two local processes:
  • the Flask pipeline API on http://127.0.0.1:5000  (scrape / draft / Gmail send)
  • the Next.js UI on           http://127.0.0.1:3000  (opens in your browser)

The browser only talks to the Next app; it proxies /py/* to Flask (see
web/next.config.ts), so there's no CORS to configure. Press Ctrl+C to stop both.

First time only, install the UI dependencies:
    cd web && npm install
"""
from __future__ import annotations

import atexit
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
UI_URL = "http://127.0.0.1:3000"


def _run_flask() -> None:
    # Import here so a missing Python dep fails with a clear message, not on import.
    from src.app import app
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)


def main() -> None:
    if not (WEB / "node_modules").exists():
        sys.exit(
            "UI dependencies aren't installed yet. Run:\n"
            "    cd web && npm install\n"
            "then start this again."
        )

    # Flask in a background thread (it's the API the UI proxies to).
    threading.Thread(target=_run_flask, daemon=True).start()

    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    # Production-quality local run: build once, then serve. For iterative UI work
    # use `npm run dev` in web/ instead.
    print("Building the UI (first run takes a minute)…")
    subprocess.run([npm, "run", "build"], cwd=WEB, check=True)
    ui = subprocess.Popen([npm, "run", "start"], cwd=WEB)
    atexit.register(ui.terminate)

    print(f"\nResearch Outreach is running at {UI_URL}")
    print("Leave this window open. Press Ctrl+C here to stop.\n")
    threading.Timer(2.0, lambda: webbrowser.open(UI_URL)).start()

    try:
        ui.wait()
    except KeyboardInterrupt:
        ui.terminate()


if __name__ == "__main__":
    main()
