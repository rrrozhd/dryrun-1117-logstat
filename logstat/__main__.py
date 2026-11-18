"""Command-line summaries of Common Log Format access logs."""

import argparse
import csv
from datetime import datetime
import io
import json
import re
import sys

from .core import summarize


def utc_timestamp(value: str) -> datetime:
    message = (
        "expected a UTC timestamp YYYY-MM-DDTHH:MM:SS[.fraction]Z or +00:00 "
        "(1–6 fractional digits)"
    )
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.[0-9]{1,6})?(?:Z|\+00:00)", value
    ):
        raise argparse.ArgumentTypeError(message)
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(message) from error


def status_codes(value: str) -> set[str]:
    if re.fullmatch(r"[1-5][0-9]{2}", value):
        return {value}
    if re.fullmatch(r"[1-5]xx", value):
        start = int(value[0]) * 100
        return {str(code) for code in range(start, start + 100)}
    raise argparse.ArgumentTypeError("expected a status code 100–599 or class 1xx–5xx")


def nonnegative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected a nonnegative integer") from error
    if number < 0:
        raise argparse.ArgumentTypeError("expected a nonnegative integer")
    return number


def format_text(summary: dict) -> str:
    """Render the same summary fields exposed by JSON output."""
    lines = [
        f"Requests: {summary['request_count']}",
        f"Malformed lines: {summary['malformed_count']}",
        f"Total bytes: {summary['total_bytes']}",
    ]
    sections = [
        ("Status codes", summary["status_counts"].items()),
        ("Top paths", ((item["path"], item["count"]) for item in summary["top_paths"])),
        ("Requests per hour (UTC)", summary["requests_per_hour"].items()),
    ]
    for title, entries in sections:
        lines.append(f"{title}:")
        rows = [f"  {key}: {count}" for key, count in entries]
        lines.extend(rows or ["  (none)"])
    return "\n".join(lines)


def format_csv(summary: dict, *, no_header: bool = False) -> str:
    """Render summary numbers as metric, key and value records."""
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    if not no_header:
        writer.writerow(("metric", "key", "value"))
    for metric in ("request_count", "malformed_count", "total_bytes"):
        writer.writerow((metric, "", summary[metric]))
    sections = [
        ("status_counts", summary["status_counts"].items()),
        ("top_paths", ((item["path"], item["count"]) for item in summary["top_paths"])),
        ("requests_per_hour", summary["requests_per_hour"].items()),
    ]
    for metric, entries in sections:
        writer.writerows((metric, key, value) for key, value in entries)
    return output.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="logstat", description="Summarize Common Log Format access logs."
    )
    parser.add_argument("logfile", help="path to the access log")
    output_options = parser.add_mutually_exclusive_group()
    output_options.add_argument("--json", action="store_true", help="print the summary as JSON")
    output_options.add_argument("--format", choices=("text", "csv"), default="text",
                                help="summary output format (default: text)")
    parser.add_argument("--no-header", action="store_true",
                        help="omit the header row (requires --format csv)")
    parser.add_argument("--since", type=utc_timestamp, metavar="UTC",
                        help="include requests at or after this UTC timestamp")
    parser.add_argument("--until", type=utc_timestamp, metavar="UTC",
                        help="include requests before this UTC timestamp")
    parser.add_argument("--status", type=status_codes, action="append", metavar="CODE|CLASS",
                        help="keep a status code or class, e.g. 404 or 5xx (repeatable)")
    parser.add_argument("--top", type=nonnegative_int, default=10, metavar="N",
                        help="show at most N top paths (nonnegative; default: 10)")
    args = parser.parse_args()
    if args.no_header and args.format != "csv":
        parser.error("--no-header requires --format csv")
    if args.since is not None and args.until is not None and args.since > args.until:
        parser.error("--since must not be later than --until")
    statuses = {code for selector in args.status for code in selector} if args.status else None

    try:
        with open(args.logfile, encoding="utf-8", errors="replace") as logfile:
            summary = summarize(logfile, since=args.since, until=args.until,
                                statuses=statuses, top=args.top)
    except OSError as error:
        print(f"logstat: {error}", file=sys.stderr)
        return 1

    limit = sys.get_int_max_str_digits()
    try:
        # A sum of valid byte counts can exceed the input conversion limit.
        # Change only output conversion, using a finite bound, then restore it.
        if limit and summary["total_bytes"] >= 10**limit:
            sys.set_int_max_str_digits(summary["total_bytes"].bit_length())
        if args.format == "csv":
            output = format_csv(summary, no_header=args.no_header)
        else:
            output = json.dumps(summary, indent=2) if args.json else format_text(summary)
    finally:
        sys.set_int_max_str_digits(limit)
    print(output, end="" if args.format == "csv" else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
