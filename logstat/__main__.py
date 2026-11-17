"""Command-line summaries of Common Log Format access logs."""

import argparse
import json
import sys

from .core import summarize


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


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="logstat", description="Summarize Common Log Format access logs."
    )
    parser.add_argument("logfile", help="path to the access log")
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = parser.parse_args()

    try:
        with open(args.logfile, encoding="utf-8", errors="replace") as logfile:
            summary = summarize(logfile)
    except OSError as error:
        print(f"logstat: {error}", file=sys.stderr)
        return 1

    limit = sys.get_int_max_str_digits()
    try:
        # A sum of valid byte counts can exceed the input conversion limit.
        # Change only output conversion, using a finite bound, then restore it.
        if limit and summary["total_bytes"] >= 10**limit:
            sys.set_int_max_str_digits(summary["total_bytes"].bit_length())
        output = json.dumps(summary, indent=2) if args.json else format_text(summary)
    finally:
        sys.set_int_max_str_digits(limit)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
