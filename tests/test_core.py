from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from logstat.core import summarize


def record(path="/", status="200", size="10", date="10/Oct/2000:13:55:36 -0700"):
    return f'127.0.0.1 - frank [{date}] "GET {path} HTTP/1.0" {status} {size}\n'


def test_mixed_records_have_exact_metrics():
    lines = [record("/a?one=1"), record("/a?two=2", "404", "-"),
             record("/b", "201", "30", "10/Oct/2000:21:05:00 +0000"), "broken\n", "\n"]
    assert summarize(iter(lines)) == {
        "request_count": 3,
        "malformed_count": 2,
        "total_bytes": 40,
        "status_counts": {"200": 1, "201": 1, "404": 1},
        "top_paths": [{"path": "/a", "count": 2}, {"path": "/b", "count": 1}],
        "requests_per_hour": {"2000-10-10T20:00:00Z": 2, "2000-10-10T21:00:00Z": 1},
    }


def test_empty_input():
    assert summarize([]) == {
        "request_count": 0, "malformed_count": 0, "total_bytes": 0,
        "status_counts": {}, "top_paths": [], "requests_per_hour": {},
    }


@pytest.mark.parametrize("line", [
    "", "\n", "not a log entry", record().replace('"GET / HTTP/1.0"', '"-"'),
    record().replace("HTTP/1.0", "bogus"), record().replace("GET /", "GET "),
    record(path='/bad"path'), record().replace("GET", 'GE"T'),
    record(date="30/Feb/2000:13:55:36 +0000"),
    record(date="10/Foo/2000:13:55:36 +0000"),
    record(date="10/Oct/2000:25:55:36 +0000"),
    record(date="10/Oct/2000:13:55:36 +2400"),
    record(date="10/Oct/2000:13:55:36 +0060"),
    record(date="01/Jan/0001:00:00:00 +0100"),
    record(date="31/Dec/9999:23:59:59 -0100"),
    record(status="99"), record(status="600"), record(status="2xx"),
    record(size="-1"), record(size="1.5"), record(size="9" * 5000),
    record().rstrip() + " extra", record().replace(" / ", " /bad\x00path "),
])
def test_malformed_line_is_counted_without_affecting_metrics(line):
    result = summarize([line, record()])
    assert result["request_count"] == 1
    assert result["malformed_count"] == 1
    assert result["total_bytes"] == 10


def test_offset_normalization_and_date_rollover():
    result = summarize([
        record(date="01/Jan/2024:00:15:00 +0100"),
        record(date="31/Dec/2023:23:45:00 +0000"),
        record(date="01/Jan/2024:00:00:00 +0000"),
    ])
    assert result["requests_per_hour"] == {
        "2023-12-31T23:00:00Z": 2, "2024-01-01T00:00:00Z": 1,
    }


def test_top_ten_ties_are_stable_and_case_and_encoding_are_preserved():
    paths = ["/z", "/a", "/A", "/%61"] + [f"/{n}" for n in range(10)]
    lines = [record(path) for path in paths] + [record("/z?x=1")]
    expected = [{"path": "/z", "count": 2}] + [
        {"path": path, "count": 1} for path in sorted(set(paths) - {"/z"})[:9]
    ]
    assert summarize(lines)["top_paths"] == expected
    assert summarize(reversed(lines)) == summarize(lines)


def test_large_bytes_crlf_and_unterminated_last_line():
    result = summarize([record(size=str(2**80)).replace("\n", "\r\n"), record(size="0").rstrip()])
    assert result["request_count"] == 2
    assert result["malformed_count"] == 0
    assert result["total_bytes"] == 2**80


def test_escaped_request_quotes_are_preserved():
    path = r'/say\"hello'
    result = summarize([record(path=path)])
    assert result["request_count"] == 1
    assert result["malformed_count"] == 0
    assert result["top_paths"] == [{"path": path, "count": 1}]


@pytest.mark.parametrize("since,until,expected_paths", [
    ("2000-10-10T20:00:00Z", "2000-10-10T20:00:02Z", {"/offset", "/utc", "/next"}),
    ("2000-10-10T20:00:00.5Z", "2000-10-10T20:00:01.5Z", {"/next"}),
    ("2000-10-10T21:00:00+01:00", "2000-10-10T13:00:01-07:00", {"/offset", "/utc"}),
    ("2000-10-10T20:00:00Z", "2000-10-10T20:00:00Z", set()),
    (None, "2000-10-10T20:00:00Z", {"/before"}),
    ("2000-10-10T20:00:02Z", None, {"/end"}),
])
def test_time_bounds_use_exact_instants(since, until, expected_paths):
    lines = [
        record("/before", date="10/Oct/2000:19:59:59 +0000"),
        record("/offset", date="10/Oct/2000:13:00:00 -0700"),
        record("/utc", date="10/Oct/2000:20:00:00 +0000"),
        record("/next", date="10/Oct/2000:20:00:01 +0000"),
        record("/end", date="10/Oct/2000:20:00:02 +0000"),
    ]
    result = summarize(iter(lines),
                       since=datetime.fromisoformat(since) if since else None,
                       until=datetime.fromisoformat(until) if until else None)
    assert result["request_count"] == len(expected_paths)
    assert {item["path"] for item in result["top_paths"]} == expected_paths
    assert result["malformed_count"] == 0


@pytest.mark.parametrize("bounds", [
    {"since": datetime(2000, 10, 10)},
    {"until": datetime(2000, 10, 10)},
    {"since": datetime.fromisoformat("2000-10-10T20:00:01Z"),
     "until": datetime.fromisoformat("2000-10-10T20:00:00Z")},
])
def test_invalid_time_bounds_are_rejected(bounds):
    with pytest.raises(ValueError):
        summarize([], **bounds)


def test_repeated_hour_bounds_compare_instants_instead_of_wall_times():
    zone = ZoneInfo("America/New_York")
    since = datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=0)
    until = datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=1)
    lines = [record(date="01/Nov/2026:06:00:00 +0000")]
    expected = summarize(lines, since=since.astimezone(timezone.utc),
                         until=until.astimezone(timezone.utc))
    assert expected["request_count"] == 1
    assert summarize(lines, since=since, until=until) == expected
    with pytest.raises(ValueError):
        summarize(lines, since=until, until=since)


def test_bound_comparison_preserves_full_datetime_range():
    since = datetime.min.replace(tzinfo=timezone(timedelta(hours=1)))
    until = datetime.max.replace(tzinfo=timezone(timedelta(hours=-1)))
    assert summarize([record()], since=since, until=until) == summarize([record()])
    with pytest.raises(ValueError):
        summarize([], since=until, until=since)


@pytest.mark.parametrize("statuses,expected_count", [(None, 1), (set(), 0)])
def test_status_none_and_empty_set_have_distinct_meanings(statuses, expected_count):
    result = summarize(iter([record(), "broken\n"]), statuses=statuses)
    assert result["request_count"] == expected_count
    assert result["malformed_count"] == 1
    assert result["total_bytes"] == 10 * expected_count
    assert result["status_counts"] == ({"200": 1} if expected_count else {})


def test_combined_filters_apply_to_every_metric():
    lines = [
        record("/early", "200", "100", "10/Oct/2000:19:59:59 +0000"),
        record("/a?x=1", "200", "5", "10/Oct/2000:20:00:00 +0000"),
        record("/a?x=2", "404", "-", "10/Oct/2000:13:30:00 -0700"),
        record("/b", "404", "7", "10/Oct/2000:21:00:00 +0000"),
        record("/wrong-status", "500", "100", "10/Oct/2000:20:01:00 +0000"),
        record("/late", "200", "100", "10/Oct/2000:22:00:00 +0000"),
        "broken\n", record(date="30/Feb/2000:12:00:00 +0000"),
    ]
    assert summarize(iter(lines), since=datetime.fromisoformat("2000-10-10T20:00:00Z"),
                     until=datetime.fromisoformat("2000-10-10T22:00:00Z"),
                     statuses={"200", "404"}) == {
        "request_count": 3, "malformed_count": 2, "total_bytes": 12,
        "status_counts": {"200": 1, "404": 2},
        "top_paths": [{"path": "/a", "count": 2}, {"path": "/b", "count": 1}],
        "requests_per_hour": {"2000-10-10T20:00:00Z": 2, "2000-10-10T21:00:00Z": 1},
    }


@pytest.mark.parametrize("filters", [
    {"statuses": set()},
    {"since": datetime.fromisoformat("2026-01-01T00:00:00Z")},
    {"since": datetime.fromisoformat("2000-10-10T20:00:00Z"),
     "until": datetime.fromisoformat("2000-10-10T20:00:00Z")},
])
def test_no_matches_still_count_all_malformed_lines(filters):
    result = summarize(iter(["broken\n", record(), "\n"]), top=0, **filters)
    assert result == {**summarize([]), "malformed_count": 2}


@pytest.mark.parametrize("top", [0, 1, 3, 20])
def test_top_limits_only_paths_and_preserves_tie_order(top):
    lines = [record(path) for path in ["/z", "/b", "/a", "/z", "/c"]] + ["broken\n"]
    default = summarize(lines)
    result = summarize(iter(lines), top=top)
    expected = [{"path": "/z", "count": 2}] + [
        {"path": path, "count": 1} for path in ["/a", "/b", "/c"]
    ]
    assert result == {**default, "top_paths": expected[:top]}


@pytest.mark.parametrize("top", [-1, 1.5, "2", None])
def test_top_rejects_negative_or_noninteger_values(top):
    with pytest.raises((TypeError, ValueError)):
        summarize([], top=top)
