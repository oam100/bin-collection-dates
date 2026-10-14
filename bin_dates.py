#!/usr/bin/env python3
"""Fetch the next bin collection dates from Sevenoaks District Council.

The council's "waste collection day" tool (a Netcall Liberty Create app on
oncreate.app) only hands out short-lived, single-use schedule links, so a
saved link can't be reused. Instead, each run replays what the page does
when you pick your address from its dropdown:

1. GET the address-lookup page to start a session (cookie).
2. POST a dummy request to get the page's widgets, and find the address
   widget's page id, cell id and token. These ids change whenever the
   council edits the page, so they're rediscovered every run rather than
   hardcoded.
3. POST "address_selected" with the property id; the reply contains a
   fresh schedule URL.
4. GET that URL and read "Your next collection date is ..." under each of
   the General waste / Recycling / Garden waste headings.

The property id is the council's internal id for the address (the `id=`
value in a schedule link), not a UPRN. It's personal (it identifies a home
address), so it's passed in via --property-id or $BIN_PROPERTY_ID rather
than stored in this file.

The council only gives the *next* date for each bin, so past collections
are carried over from the previous output (the -o JSON) to keep them in the
calendar; the live site always wins for today and later. Future dates are
never estimated.

Fails loudly (non-zero exit, nothing written) if any of the three dates
can't be found, so a site change never silently publishes empty data.
"""

import argparse
import html
import http.cookiejar
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

BASE = "https://sevenoaks-dc-host01.oncreate.app"
LOOKUP_PATH = "/w/webpage/waste-collection-day"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)

# Output key -> heading text on the schedule page (also used as the label).
BIN_TYPES = {
    "general": "General waste",
    "recycling": "Recycling",
    "garden": "Garden waste",
}

INPUT_NAME_RE = re.compile(r"payload\[(PAG\w+)\]\[(PWG\w+)\]\[(PCL\w+)\]")
WIDGET_TOKEN_RE = re.compile(r"webpage_token=([0-9a-f]+)")


class ScrapeError(Exception):
    pass


class Session:
    def __init__(self, timeout: int = 20):
        self.timeout = timeout
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def request(self, path_or_url: str, data: dict | None = None, xhr: bool = False) -> str:
        url = path_or_url if path_or_url.startswith("http") else BASE + path_or_url
        headers = {"User-Agent": USER_AGENT}
        if xhr:
            headers["X-Requested-With"] = "XMLHttpRequest"
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body, headers=headers)
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                return resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
        except OSError as e:  # URLError, HTTPError and timeouts are all OSErrors
            raise ScrapeError(f"Request to {url.split('?')[0]} failed: {e}")

    def request_json(self, path_or_url: str, data: dict | None = None) -> dict:
        text = self.request(path_or_url, data=data, xhr=True)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise ScrapeError(f"Expected JSON from {path_or_url.split('?')[0]}, got: {text[:200]!r}")


def find_address_widget(widgets_html: str) -> tuple[str, str, str]:
    """Return (page_id, cell_id, token) for the address-lookup widget.

    The widget's Vue config is an HTML-escaped JSON attribute containing a
    `postcode_search` field, an `input_name` of the form
    payload[PAGE][GROUP][CELL] and a `widget_url` carrying the page token.
    """
    unescaped = html.unescape(widgets_html)
    idx = unescaped.find('"postcode_search"')
    if idx == -1:
        raise ScrapeError("Address lookup widget not found (no postcode_search) - page structure may have changed")
    # input_name and widget_url follow postcode_search in the same config
    # blob. Search forwards only: earlier widgets carry different tokens.
    blob = unescaped[idx: idx + 3000]
    name = INPUT_NAME_RE.search(blob)
    token = WIDGET_TOKEN_RE.search(blob)
    if not name or not token:
        raise ScrapeError("Address widget found but its page/cell ids or token are missing")
    page_id, _group_id, cell_id = name.groups()
    return page_id, cell_id, token.group(1)


def get_schedule_html(property_id: str) -> str:
    s = Session()
    s.request(LOOKUP_PATH)
    widgets = s.request_json(LOOKUP_PATH, data={"_dummy": "1"})
    page_id, cell_id, token = find_address_widget(widgets.get("data") or "")

    query = urllib.parse.urlencode(
        {"webpage_subpage_id": page_id, "webpage_token": token, "widget_action": "handle_event"}
    )
    selected = s.request_json(
        f"{LOOKUP_PATH}?{query}",
        data={
            "code_action": "address_selected",
            "code_params": json.dumps({"selected": property_id}),
            "action_cell_id": cell_id,
            "action_page_id": page_id,
        },
    )
    schedule_url = (selected.get("response") or {}).get("url")
    if not schedule_url:
        raise ScrapeError(f"No schedule URL returned for property {property_id} - wrong id, or the flow changed")

    schedule = s.request_json(schedule_url)
    return schedule.get("data") or ""


def html_to_text(fragment: str) -> str:
    fragment = re.sub(r"<script.*?</script>", " ", fragment, flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", fragment))
    return re.sub(r"\s+", " ", text)


def parse_dates(schedule_html: str) -> dict[str, date]:
    """Extract {key: next collection date} for every type in BIN_TYPES.

    Each section reads "<Heading> Your next collection date is
    [NextCollectionDate] Thursday, 15 October 2026". The same headings also
    appear earlier in a debug table, which this pattern doesn't match.
    """
    text = html_to_text(schedule_html)
    dates = {}
    for key, heading in BIN_TYPES.items():
        match = re.search(
            re.escape(heading)
            + r" Your next collection date is (?:NextCollectionDate )?([A-Za-z]+, \d{1,2} [A-Za-z]+ \d{4})",
            text,
        )
        if not match:
            raise ScrapeError(f"No next collection date found for {heading!r} - page structure may have changed")
        dates[key] = datetime.strptime(match.group(1), "%A, %d %B %Y").date()
    return dates


def merge_with_previous(next_dates: dict[str, date], output_path: str, today: date) -> list[tuple[str, date]]:
    """Past collections from the previous output, plus the current next dates.

    Only previous entries before today are kept: anything from today on
    comes from the live site, so a rescheduled collection (e.g. around a
    bank holiday) replaces the old date rather than leaving both.
    """
    try:
        with open(output_path) as f:
            previous = json.load(f).get("collections", [])
    except (FileNotFoundError, json.JSONDecodeError):
        previous = []
    collections = {
        (entry["type"], date.fromisoformat(entry["date"]))
        for entry in previous
        if entry.get("type") in BIN_TYPES and date.fromisoformat(entry["date"]) < today
    }
    collections.update(next_dates.items())
    return sorted(collections, key=lambda c: (c[1], c[0]))


def ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")


def write_ics(collections: list[tuple[str, date]], path: str) -> None:
    """One all-day event per collection."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//bin-collection-dates//EN",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:Bin collections",
    ]
    for key, d in collections:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{key}-{d.isoformat()}@bin-collection-dates",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}",
            f"SUMMARY:{ics_escape(BIN_TYPES[key] + ' collection')}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    with open(path, "w", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--property-id", default=os.environ.get("BIN_PROPERTY_ID"),
        help="council property id (the id= in a schedule link); default: $BIN_PROPERTY_ID",
    )
    parser.add_argument("-o", "--output", default="bin_dates.json", help="output JSON path (also read for past collections)")
    parser.add_argument("--ics", default=None, help="also write an .ics calendar to this path")
    args = parser.parse_args(argv)

    if not args.property_id:
        parser.error("--property-id or $BIN_PROPERTY_ID is required")

    try:
        dates = parse_dates(get_schedule_html(args.property_id))
    except ScrapeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # No fetch timestamp in the JSON: it's committed by the workflow only
    # when it changes, so it must only change when the dates do.
    collections = merge_with_previous(dates, args.output, date.today())
    result = {
        "next_collections": {key: d.isoformat() for key, d in dates.items()},
        "collections": [{"type": key, "date": d.isoformat()} for key, d in collections],
    }
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)
        f.write("\n")
    if args.ics:
        write_ics(collections, args.ics)

    for key, d in sorted(dates.items(), key=lambda kv: (kv[1], kv[0])):
        print(f"{BIN_TYPES[key]:<14} {d:%a %d %b %Y}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
