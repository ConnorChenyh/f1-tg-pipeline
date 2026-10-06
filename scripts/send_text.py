#!/usr/bin/env python3
"""Send a plain-text file to the configured Telegram chat.

Used for short operational notes such as the daily review summary. Text longer
than one Telegram message is split on paragraph boundaries instead of being
truncated.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from publisher.telegram import TELEGRAM_MESSAGE_LIMIT
from publisher.telegram_text import send_text_to_telegram
from run import DEFAULT_CONFIG, load_config


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split on blank lines, then lines, then hard-cut only as a last resort."""
    # (separator before the piece, piece), each piece already within the limit.
    units: list[tuple[str, str]] = []
    for paragraph in text.strip().split("\n\n"):
        lines = [paragraph] if len(paragraph) <= limit else paragraph.split("\n")
        for line_index, line in enumerate(lines):
            separator = "\n\n" if line_index == 0 else "\n"
            for start in range(0, max(len(line), 1), limit):
                units.append((separator if start == 0 else "", line[start:start + limit]))

    chunks: list[str] = []
    current = ""
    for separator, piece in units:
        candidate = f"{current}{separator}{piece}" if current else piece
        if len(candidate) <= limit:
            current = candidate
        else:
            chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", type=Path, help="UTF-8 text file to send")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Path to config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Print the messages instead of sending")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    text = args.file.read_text(encoding="utf-8")
    if not text.strip():
        print(f"{args.file} is empty; nothing sent", file=sys.stderr)
        return 1
    config = load_config(args.config)
    messages = split_message(text)
    for message in messages:
        if args.dry_run:
            print(message, end="\n---\n")
        else:
            send_text_to_telegram(message, config)
    print(f"{'Prepared' if args.dry_run else 'Sent'} {len(messages)} message(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
