"""Parse Common Log Format records and compute deterministic summary metrics."""

import re
from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone


_MONTHS = {name: number for number, name in enumerate(
    "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), start=1
)}
_RECORD = re.compile(
    r'\S+ \S+ \S+ \[(\d{2})/([A-Za-z]{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2}) '
    r'([+-])(\d{2})(\d{2})\] "\S+ (\S+) HTTP/\d+\.\d+" ([1-5]\d{2}) (\d+|-)',
    re.ASCII,
)


def _parse(line: str) -> tuple[str, str, int, str] | None:
    line = line.rstrip("\r\n")
    if any(ord(char) < 32 or ord(char) == 127 or char == "\ufffd" for char in line):
        return None
    match = _RECORD.fullmatch(line)
    if match is None:
        return None
    day, month, year, hour, minute, second, sign, offset_hour, offset_minute, target, status, size = match.groups()
    try:
        offset_hours, offset_minutes = int(offset_hour), int(offset_minute)
        if offset_hours > 23 or offset_minutes > 59:
            return None
        offset = timedelta(hours=offset_hours, minutes=offset_minutes)
        if sign == "-":
            offset = -offset
        timestamp = datetime(
            int(year), _MONTHS.get(month, 0), int(day), int(hour), int(minute), int(second),
            tzinfo=timezone(offset),
        ).astimezone(timezone.utc)
        byte_count = 0 if size == "-" else int(size)
    except (ValueError, OverflowError):
        return None
    path = target.split("?", 1)[0]
    if not path:
        return None
    hour_key = timestamp.replace(minute=0, second=0).isoformat().replace("+00:00", "Z")
    return path, status, byte_count, hour_key


def summarize(lines: Iterable[str]) -> dict:
    """Count valid records, skipping malformed lines, and group requests in UTC."""
    request_count = malformed_count = total_bytes = 0
    statuses: Counter[str] = Counter()
    paths: Counter[str] = Counter()
    hours: Counter[str] = Counter()
    for line in lines:
        record = _parse(line)
        if record is None:
            malformed_count += 1
            continue
        path, status, byte_count, hour = record
        request_count += 1
        total_bytes += byte_count
        statuses[status] += 1
        paths[path] += 1
        hours[hour] += 1
    return {
        "request_count": request_count,
        "malformed_count": malformed_count,
        "total_bytes": total_bytes,
        "status_counts": dict(sorted(statuses.items())),
        "top_paths": [
            {"path": path, "count": count}
            for path, count in sorted(paths.items(), key=lambda item: (-item[1], item[0]))[:10]
        ],
        "requests_per_hour": dict(sorted(hours.items())),
    }
