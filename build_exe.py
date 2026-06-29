"""Build the standalone desktop app with PyInstaller.

Produces a single double-click file your friends can run with NO Python and NO
folder:
  - Windows:  dist/ResearchOutreach.exe
  - macOS:    dist/ResearchOutreach.app  (and a CLI binary alongside)

Run on the OS you're targeting (a Windows build runs only on Windows; build the
Mac version on a Mac):

    pip install -r requirements.txt -r requirements-desktop.txt
    python build_exe.py

IMPORTANT: create your .env first (copy .env.example, set ANTHROPIC_API_KEY).
The .env is bundled into the app so friends share your key — keep a spend limit
on it in the Anthropic console.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEP = os.pathsep  # ';' on Windows, ':' on macOS/Linux — PyInstaller --add-data separator

# (source path, destination inside the bundle). Destinations must match what
# config.bundled_dir() / bootstrap() expect: templates/, static/, ., data/.
ADD_DATA = [
    ("config.yaml", "."),
    (".env", "."),
    ("data/profile.md", "data"),
    ("data/directory_urls.txt", "data"),
    ("src/templates", "templates"),
    ("src/static", "static"),
]


def main() -> None:
    if not (ROOT / ".env").exists():
        sys.exit(
            "No .env found. Copy .env.example to .env and set ANTHROPIC_API_KEY "
            "before building (it gets bundled into the app)."
        )
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("PyInstaller not installed. Run: pip install -r requirements-desktop.txt")

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--windowed",                 # no console window
        "--name", "ResearchOutreach",
    ]
    for src, dst in ADD_DATA:
        if not (ROOT / src).exists():
            sys.exit(f"Missing file to bundle: {src}")
        args += ["--add-data", f"{src}{SEP}{dst}"]

    # Make sure dynamically-imported packages get pulled in fully.
    for pkg in ("anthropic", "certifi", "webview", "pypdf"):
        args += ["--collect-all", pkg]

    args.append("desktop.py")

    print("Running:\n  " + " ".join(args) + "\n")
    subprocess.check_call(args, cwd=str(ROOT))

    out = "dist/ResearchOutreach.exe" if os.name == "nt" else "dist/ResearchOutreach.app"
    print(f"\nDone. Your shareable app is at: {out}")
    print("Send that single file to your friends — they don't need Python or the folder.")


if __name__ == "__main__":
    main()
