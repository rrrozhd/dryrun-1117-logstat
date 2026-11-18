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
    r'([+-])(\d{2})(\d{2})\] "[^"\s]+ ((?:\\.|[^"\\\s])+) HTTP/\d+\.\d+" ([1-5]\d{2}) (\d+|-)',
    re.ASCII,
)


def _parse(line: str) -> tuple[str, str, int, datetime] | None:
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
    return path, status, byte_count, timestamp


def summarize(
    lines: Iterable[str], *, since: datetime | None = None,
    until: datetime | None = None, statuses: set[str] | None = None, top: int = 10,
) -> dict:
    """Summarize matching records within [since, until), grouped in UTC.

    Bounds must be timezone-aware. None selects all statuses; an empty set selects
    none. Malformed counts cover all input; top limits only the returned paths.
    """
    for name, bound in (("since", since), ("until", until)):
        if bound is not None and bound.utcoffset() is None:
            raise ValueError(f"{name} must be timezone-aware")
    if since is not None and until is not None:
        # Same-zone datetime comparisons ignore fold. Compare UTC elapsed time
        # without converting endpoints, which can overflow at datetime limits.
        elapsed = until.replace(tzinfo=None) - since.replace(tzinfo=None)
        if elapsed < until.utcoffset() - since.utcoffset():
            raise ValueError("since must not be later than until")
    if not isinstance(top, int) or top < 0:
        raise ValueError("top must be a nonnegative integer")
    request_count = malformed_count = total_bytes = 0
    status_counts: Counter[str] = Counter()
    paths: Counter[str] = Counter()
    hours: Counter[str] = Counter()
    for line in lines:
        record = _parse(line)
        if record is None:
            malformed_count += 1
            continue
        path, status, byte_count, timestamp = record
        if since is not None and timestamp < since:
            continue
        if until is not None and timestamp >= until:
            continue
        if statuses is not None and status not in statuses:
            continue
        hour = timestamp.replace(minute=0, second=0).isoformat().replace("+00:00", "Z")
        request_count += 1
        total_bytes += byte_count
        status_counts[status] += 1
        paths[path] += 1
        hours[hour] += 1
    return {
        "request_count": request_count,
        "malformed_count": malformed_count,
        "total_bytes": total_bytes,
        "status_counts": dict(sorted(status_counts.items())),
        "top_paths": [
            {"path": path, "count": count}
            for path, count in sorted(paths.items(), key=lambda item: (-item[1], item[0]))[:top]
        ],
        "requests_per_hour": dict(sorted(hours.items())),
    }
