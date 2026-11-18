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
