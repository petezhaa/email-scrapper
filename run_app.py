"""Launch the local web UI and open it in your browser.

    python run_app.py

Then go to http://127.0.0.1:5000 (it opens automatically).
Press Ctrl+C in this window to stop the app.
"""
import threading
import webbrowser

from src.app import app

URL = "http://127.0.0.1:5000"


def _open_browser() -> None:
    webbrowser.open(URL)


if __name__ == "__main__":
    print(f"\nResearch Outreach is running at {URL}")
    print("Leave this window open. Press Ctrl+C here to stop.\n")
    # Open the browser a moment after the server starts.
    threading.Timer(1.2, _open_browser).start()
    # 127.0.0.1 only — not reachable from other machines on the network.
    app.run(host="127.0.0.1", port=5000, debug=False)
