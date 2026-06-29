"""Command-line entry point.

    python -m src.cli scrape            # faculty directory URLs -> data/targets.csv
    python -m src.cli draft [--limit N] # targets.csv -> personalized drafts/
    python -m src.cli status            # list drafts and their status
    python -m src.cli send [--send]     # send approved drafts (dry run unless --send)
"""
from __future__ import annotations

import argparse
import sys

from . import draft, scrape, send
from .config import PipelineError


def main() -> None:
    # Drafts/emails can contain arbitrary Unicode (em-dashes, accented names).
    # Windows terminals default to cp1252 and crash on print — force UTF-8.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(prog="email-outreach", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scrape", help="scrape faculty directory URLs into targets.csv")

    p_draft = sub.add_parser("draft", help="generate personalized draft emails")
    p_draft.add_argument("--limit", type=int, default=None, help="only draft the next N targets")

    sub.add_parser("status", help="list all drafts and their status")

    p_send = sub.add_parser("send", help="send approved drafts (dry run by default)")
    p_send.add_argument("--send", action="store_true", help="actually send (otherwise preview only)")

    args = parser.parse_args()

    try:
        if args.command == "scrape":
            scrape.run()
        elif args.command == "draft":
            draft.run(limit=args.limit)
        elif args.command == "status":
            send.status()
        elif args.command == "send":
            send.run(do_send=args.send)
    except PipelineError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
