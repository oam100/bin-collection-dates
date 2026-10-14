#!/usr/bin/env python3
"""Send an Alexa notification (via the Notify Me skill) on bin night.

Reads bin_dates.json (written by bin_dates.py) and, if any bins are
collected tomorrow (UK time), sends e.g. "Bin night: put the rubbish and
garden waste out tonight." through https://www.thomptronics.com/about/notify-me.
Nothing is sent when there's no collection tomorrow.

The Notify Me access code comes from $NOTIFYME_ACCESS_CODE. It lets anyone
post to the user's Echo, so it's kept in a repository secret, never here.
"""

import argparse
import json
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

NOTIFYME_URL = "https://api.notifymyecho.com/v1/NotifyMe"
UK = ZoneInfo("Europe/London")

# bin_dates.py key -> spoken name, in the order they're listed.
SPOKEN_NAMES = {
    "general": "rubbish",
    "recycling": "recycling",
    "garden": "garden waste",
}


def bins_due(path: str, day: date) -> list[str]:
    with open(path) as f:
        collections = json.load(f).get("collections", [])
    due = {c["type"] for c in collections if c.get("date") == day.isoformat()}
    return [key for key in SPOKEN_NAMES if key in due]


def join_words(words: list[str]) -> str:
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def build_message(keys: list[str], day: date) -> str:
    names = join_words([SPOKEN_NAMES[k] for k in keys])
    return f"Bin night: put the {names} out tonight. Collection is tomorrow, {day:%A %-d %B}."


def send_notification(message: str, access_code: str, timeout: int = 20) -> None:
    body = json.dumps({"notification": message, "accessCode": access_code, "title": "Bin night"}).encode()
    req = urllib.request.Request(
        NOTIFYME_URL, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-i", "--input", default="bin_dates.json", help="bin_dates.py output JSON")
    parser.add_argument(
        "--date", type=date.fromisoformat, default=None,
        help="collection date to check (default: tomorrow, UK time)",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the message instead of sending it")
    args = parser.parse_args(argv)

    day = args.date or datetime.now(UK).date() + timedelta(days=1)
    keys = bins_due(args.input, day)
    if not keys:
        print(f"No collection on {day:%a %d %b %Y} - nothing to send")
        return 0

    message = build_message(keys, day)
    if args.dry_run:
        print(f"Would send: {message}")
        return 0

    access_code = os.environ.get("NOTIFYME_ACCESS_CODE")
    if not access_code:
        print("Error: $NOTIFYME_ACCESS_CODE is not set", file=sys.stderr)
        return 1
    try:
        send_notification(message, access_code)
    except OSError as e:
        print(f"Error: Notify Me request failed: {e}", file=sys.stderr)
        return 1
    print(f"Sent: {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
