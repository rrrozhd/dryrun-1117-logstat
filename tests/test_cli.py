import csv
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
VALID_LINE = (
    '127.0.0.1 - - [10/Oct/2000:13:55:36 -0700] '
    '"GET /alpha HTTP/1.0" 200 10\n'
)
EMPTY_SUMMARY = {
    "request_count": 0,
    "malformed_count": 0,
    "total_bytes": 0,
    "status_counts": {},
    "top_paths": [],
    "requests_per_hour": {},
}


def run_cli(*args, text=True):
    return subprocess.run(
        [sys.executable, "-m", "logstat", *map(str, args)],
        cwd=ROOT,
        capture_output=True,
        text=text,
        timeout=10,
    )


def assert_csv_header_suppression(path, *args):
    normal = run_cli(path, "--format", "csv", *args, text=False)
    headerless = run_cli(path, "--format", "csv", "--no-header", *args, text=False)

    assert normal.returncode == 0, normal.stderr
    assert headerless.returncode == 0, headerless.stderr
    assert normal.stderr == headerless.stderr == b""
    normal_rows = list(csv.reader(normal.stdout.decode("utf-8").splitlines()))
    headerless_rows = list(csv.reader(headerless.stdout.decode("utf-8").splitlines()))
    assert normal_rows.count(["metric", "key", "value"]) == 1
    assert normal.stdout.startswith(b"metric,key,value\n")
    assert headerless.stdout == normal.stdout.partition(b"\n")[2]
    assert headerless_rows == normal_rows[1:]
    return headerless_rows


@pytest.fixture
def mixed_log(tmp_path):
    path = tmp_path / "access.log"
    path.write_text(
        VALID_LINE
        + '127.0.0.2 - - [10/Oct/2000:13:58:00 -0700] '
        '"GET /beta HTTP/1.0" 404 -\n'
        + '127.0.0.3 - - [10/Oct/2000:21:01:00 +0000] '
        '"POST /alpha HTTP/1.1" 200 25\n'
        + "\nnot a log record\n",
        encoding="utf-8",
    )
    return path


def test_json_summary_matches_hand_counted_log(mixed_log):
    result = run_cli(mixed_log, "--json")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "request_count": 3,
        "malformed_count": 2,
        "total_bytes": 35,
        "status_counts": {"200": 2, "404": 1},
        "top_paths": [{"path": "/alpha", "count": 2}, {"path": "/beta", "count": 1}],
        "requests_per_hour": {"2000-10-10T20:00:00Z": 2, "2000-10-10T21:00:00Z": 1},
    }


@pytest.mark.parametrize("args", [(), ("--format", "text")])
def test_default_and_explicit_text_summary_match_hand_counted_log(mixed_log, args):
    result = run_cli(mixed_log, *args)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout == (
        "Requests: 3\n"
        "Malformed lines: 2\n"
        "Total bytes: 35\n"
        "Status codes:\n"
        "  200: 2\n"
        "  404: 1\n"
        "Top paths:\n"
        "  /alpha: 2\n"
        "  /beta: 1\n"
        "Requests per hour (UTC):\n"
        "  2000-10-10T20:00:00Z: 2\n"
        "  2000-10-10T21:00:00Z: 1\n"
    )


def test_csv_summary_matches_hand_counted_log(mixed_log):
    result = run_cli(mixed_log, "--format", "csv")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert list(csv.reader(result.stdout.splitlines())) == [
        ["metric", "key", "value"],
        ["request_count", "", "3"],
        ["malformed_count", "", "2"],
        ["total_bytes", "", "35"],
        ["status_counts", "200", "2"],
        ["status_counts", "404", "1"],
        ["top_paths", "/alpha", "2"],
        ["top_paths", "/beta", "1"],
        ["requests_per_hour", "2000-10-10T20:00:00Z", "2"],
        ["requests_per_hour", "2000-10-10T21:00:00Z", "1"],
    ]


@pytest.mark.parametrize("request_path", ["/comma,path", r'/escaped\"quote', r'/both,\"quoted\"'])
def test_csv_path_quoting_round_trips(tmp_path, request_path):
    path = tmp_path / "quoted.log"
    path.write_text(VALID_LINE.replace("/alpha", request_path), encoding="utf-8")

    result = run_cli(path, "--format", "csv")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    rows = list(csv.reader(result.stdout.splitlines()))
    assert [row for row in rows if row[0] == "top_paths"] == [["top_paths", request_path, "1"]]


@pytest.mark.parametrize("args", [
    (),
    ("--since", "2026-11-17T15:00:00Z", "--until", "2026-11-17T15:30:00Z",
     "--status", "2xx", "--top", "1"),
    ("--status", "2xx", "--status", "200", "--status", "404", "--status", "404"),
    ("--top", "0"),
    ("--status", "500"),
])
def test_no_header_preserves_sample_data_and_filters(args):
    assert_csv_header_suppression("sample/access.log", *args)


@pytest.mark.parametrize("request_path", ["/comma,path", r'/escaped\"quote', r'/both,\"quoted\"'])
def test_no_header_preserves_quoted_keys(tmp_path, request_path):
    path = tmp_path / "quoted.log"
    path.write_text(VALID_LINE.replace("/alpha", request_path), encoding="utf-8")

    rows = assert_csv_header_suppression(path)

    assert [row for row in rows if row[0] == "top_paths"] == [["top_paths", request_path, "1"]]


@pytest.mark.parametrize("contents,malformed", [("", 0), ("broken\n\n", 2)])
def test_no_header_preserves_empty_scalar_rows(tmp_path, contents, malformed):
    path = tmp_path / "empty.log"
    path.write_text(contents, encoding="utf-8")

    assert assert_csv_header_suppression(path) == [
        ["request_count", "", "0"], ["malformed_count", "", str(malformed)],
        ["total_bytes", "", "0"],
    ]


def test_no_header_can_precede_csv_format():
    before = run_cli("--no-header", "--format", "csv", "sample/access.log")
    after = run_cli("sample/access.log", "--format", "csv", "--no-header")

    assert before.returncode == after.returncode == 0
    assert before.stderr == after.stderr == ""
    assert before.stdout == after.stdout
    assert before.stdout.startswith("request_count,,6\n")


@pytest.mark.parametrize("output_args", [(), ("--format", "text"), ("--json",)])
@pytest.mark.parametrize("no_header_first", [False, True])
def test_no_header_requires_csv_before_input_access(tmp_path, output_args, no_header_first):
    args = ["--no-header", *output_args] if no_header_first else [*output_args, "--no-header"]
    missing = tmp_path / "missing.log"
    result = run_cli(*args, missing) if no_header_first else run_cli(missing, *args)

    assert result.returncode == 2
    assert result.stdout == ""
    assert "usage:" in result.stderr.lower()
    assert "--no-header requires --format csv" in result.stderr
    assert "unrecognized arguments" not in result.stderr.lower()
    assert str(missing) not in result.stderr
    assert "traceback" not in result.stderr.lower()


@pytest.mark.parametrize("csv_output", [False, True], ids=["json", "csv"])
@pytest.mark.parametrize("contents,malformed", [("", 0), ("broken\n\n", 2)])
def test_empty_and_all_malformed_files_succeed(tmp_path, contents, malformed, csv_output):
    path = tmp_path / "access.log"
    path.write_text(contents, encoding="utf-8")

    result = run_cli(path, *(["--format", "csv"] if csv_output else ["--json"]))

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    if csv_output:
        assert list(csv.reader(result.stdout.splitlines())) == [
            ["metric", "key", "value"], ["request_count", "", "0"],
            ["malformed_count", "", str(malformed)], ["total_bytes", "", "0"],
        ]
    else:
        assert json.loads(result.stdout) == {**EMPTY_SUMMARY, "malformed_count": malformed}


def test_empty_text_sections_are_explicit(tmp_path):
    path = tmp_path / "empty.log"
    path.write_text("", encoding="utf-8")

    result = run_cli(path)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout == (
        "Requests: 0\n"
        "Malformed lines: 0\n"
        "Total bytes: 0\n"
        "Status codes:\n"
        "  (none)\n"
        "Top paths:\n"
        "  (none)\n"
        "Requests per hour (UTC):\n"
        "  (none)\n"
    )


@pytest.mark.parametrize("use_directory", [False, True], ids=["missing", "directory"])
def test_unreadable_input_reports_concise_error(tmp_path, use_directory):
    path = tmp_path if use_directory else tmp_path / "missing.log"

    result = run_cli(path, "--json")

    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr.strip()
    assert len(result.stderr.splitlines()) <= 2
    assert "traceback" not in result.stderr.lower()


@pytest.mark.parametrize("args", [(), ("--unknown",)])
def test_invalid_arguments_report_usage(tmp_path, args):
    path = tmp_path / "empty.log"
    path.write_text("", encoding="utf-8")
    result = run_cli(path, *args) if args else run_cli()

    assert result.returncode != 0
    assert result.stdout == ""
    assert "usage:" in result.stderr.lower()
    assert "traceback" not in result.stderr.lower()


def test_help_describes_input_and_output_options():
    result = run_cli("--help")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "usage:" in result.stdout.lower()
    assert "logfile" in result.stdout.lower()
    assert "--json" in result.stdout
    assert "--format" in result.stdout
    assert "--no-header" in result.stdout
    assert "csv" in result.stdout


def test_damaged_utf8_line_does_not_hide_valid_requests(tmp_path):
    path = tmp_path / "damaged.log"
    path.write_bytes(b"\xff\xfe\n" + VALID_LINE.encode("utf-8"))

    result = run_cli(path, "--json")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    summary = json.loads(result.stdout)
    assert summary["request_count"] == 1
    assert summary["malformed_count"] == 1
    assert summary["total_bytes"] == 10


@pytest.mark.parametrize("output_format,no_header", [
    ("text", False), ("json", False), ("csv", False), ("csv", True),
])
def test_large_accumulated_bytes_remain_exact(tmp_path, monkeypatch, output_format, no_header):
    monkeypatch.setenv("PYTHONINTMAXSTRDIGITS", "640")
    prefix = VALID_LINE.rsplit(" ", 1)[0] + " "
    valid = prefix + "9" * 640 + "\n"
    oversized = prefix + "9" * 641 + "\n"
    path = tmp_path / "large.log"
    path.write_text(valid * 2 + oversized, encoding="utf-8")

    args = ["--json"] if output_format == "json" else ["--format", output_format]
    if no_header:
        args.append("--no-header")
    result = run_cli(path, *args)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    expected_total = "1" + "9" * 639 + "8"
    if output_format == "json":
        summary = json.loads(result.stdout, parse_int=str)
        assert summary["request_count"] == "2"
        assert summary["malformed_count"] == "1"
        assert summary["total_bytes"] == expected_total
    elif output_format == "csv":
        rows = list(csv.reader(result.stdout.splitlines()))
        if not no_header:
            assert rows.pop(0) == ["metric", "key", "value"]
        assert rows[:3] == [
            ["request_count", "", "2"],
            ["malformed_count", "", "1"], ["total_bytes", "", expected_total],
        ]
    else:
        assert "Requests: 2\nMalformed lines: 1\n" in result.stdout
        assert f"Total bytes: {expected_total}\n" in result.stdout


@pytest.mark.parametrize("json_output", [False, True], ids=["text", "json"])
@pytest.mark.parametrize("args,requests,total_bytes", [
    (("--since", "2026-11-17T15:00:00Z"), 3, 135),
    (("--until", "2026-11-17T15:00:00Z"), 3, 820),
    (("--status", "404"), 1, 45),
    (("--status", "2xx"), 5, 910),
])
def test_sample_filters_in_both_formats(args, requests, total_bytes, json_output):
    result = run_cli("sample/access.log", *args, *(["--json"] if json_output else []))

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    if json_output:
        summary = json.loads(result.stdout)
        assert summary["request_count"] == requests
        assert summary["total_bytes"] == total_bytes
        assert summary["malformed_count"] == 1
    else:
        assert result.stdout.startswith(
            f"Requests: {requests}\nMalformed lines: 1\nTotal bytes: {total_bytes}\n"
        )


@pytest.mark.parametrize("csv_output", [False, True], ids=["json", "csv"])
def test_all_filters_intersect(csv_output):
    args = ["--format", "csv"] if csv_output else ["--json"]
    result = run_cli("sample/access.log", *args, "--since", "2026-11-17T15:00:00Z",
                     "--until", "2026-11-17T15:30:00Z", "--status", "2xx", "--top", "1")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    if csv_output:
        assert list(csv.reader(result.stdout.splitlines())) == [
            ["metric", "key", "value"], ["request_count", "", "1"],
            ["malformed_count", "", "1"], ["total_bytes", "", "90"],
            ["status_counts", "201", "1"], ["top_paths", "/checkout", "1"],
            ["requests_per_hour", "2026-11-17T15:00:00Z", "1"],
        ]
        return
    assert json.loads(result.stdout) == {
        "request_count": 1, "malformed_count": 1, "total_bytes": 90,
        "status_counts": {"201": 1},
        "top_paths": [{"path": "/checkout", "count": 1}],
        "requests_per_hour": {"2026-11-17T15:00:00Z": 1},
    }


@pytest.mark.parametrize("csv_output", [False, True], ids=["json", "csv"])
def test_repeated_statuses_form_a_union_without_double_counting(csv_output):
    args = ["--format", "csv"] if csv_output else ["--json"]
    result = run_cli("sample/access.log", *args, "--status", "2xx", "--status", "200",
                     "--status", "404", "--status", "404")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    if csv_output:
        assert list(csv.reader(result.stdout.splitlines()))[:8] == [
            ["metric", "key", "value"], ["request_count", "", "6"],
            ["malformed_count", "", "1"], ["total_bytes", "", "955"],
            ["status_counts", "200", "3"], ["status_counts", "201", "1"],
            ["status_counts", "204", "1"], ["status_counts", "404", "1"],
        ]
        return
    summary = json.loads(result.stdout)
    assert summary["request_count"] == 6
    assert summary["total_bytes"] == 955
    assert summary["status_counts"] == {"200": 3, "201": 1, "204": 1, "404": 1}


@pytest.mark.parametrize("output_format", ["text", "json", "csv"])
@pytest.mark.parametrize("top", [0, 3])
def test_top_limits_only_paths_with_deterministic_ties(top, output_format):
    args = ["--json"] if output_format == "json" else ["--format", output_format]
    result = run_cli("sample/access.log", "--top", top, *args)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    paths = [{"path": "/products", "count": 2}, {"path": "/", "count": 1},
             {"path": "/checkout", "count": 1}][:top]
    if output_format == "json":
        summary = json.loads(result.stdout)
        assert summary["top_paths"] == paths
        assert summary["request_count"] == 6
        assert summary["total_bytes"] == 955
        assert summary["requests_per_hour"] == {
            "2026-11-17T14:00:00Z": 3, "2026-11-17T15:00:00Z": 3,
        }
    elif output_format == "csv":
        assert list(csv.reader(result.stdout.splitlines())) == [
            ["metric", "key", "value"], ["request_count", "", "6"],
            ["malformed_count", "", "1"], ["total_bytes", "", "955"],
            ["status_counts", "200", "3"], ["status_counts", "201", "1"],
            ["status_counts", "204", "1"], ["status_counts", "404", "1"],
        ] + [["top_paths", item["path"], str(item["count"])] for item in paths] + [
            ["requests_per_hour", "2026-11-17T14:00:00Z", "3"],
            ["requests_per_hour", "2026-11-17T15:00:00Z", "3"],
        ]
    else:
        assert result.stdout.startswith("Requests: 6\nMalformed lines: 1\nTotal bytes: 955\n")
        rows = "".join(f"  {item['path']}: {item['count']}\n" for item in paths) or "  (none)\n"
        assert f"Top paths:\n{rows}Requests per hour (UTC):\n" in result.stdout


@pytest.mark.parametrize("csv_output", [False, True], ids=["json", "csv"])
@pytest.mark.parametrize("args", [
    ("--status", "500"),
    ("--since", "2026-11-18T00:00:00Z"),
    ("--since", "2026-11-17T15:00:00Z", "--until", "2026-11-17T15:00:00Z"),
])
def test_filters_with_no_matches_preserve_malformed_count(args, csv_output):
    output_args = ["--format", "csv"] if csv_output else ["--json"]
    result = run_cli("sample/access.log", *output_args, *args)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    if csv_output:
        assert list(csv.reader(result.stdout.splitlines())) == [
            ["metric", "key", "value"], ["request_count", "", "0"],
            ["malformed_count", "", "1"], ["total_bytes", "", "0"],
        ]
    else:
        assert json.loads(result.stdout) == {**EMPTY_SUMMARY, "malformed_count": 1}


@pytest.mark.parametrize("flag", ["--since", "--until"])
@pytest.mark.parametrize("suffix", ["Z", "+00:00"] + ["." + "0" * n + "1Z" for n in range(6)])
def test_utc_timestamp_spellings_and_fractional_boundaries(flag, suffix):
    result = run_cli("sample/access.log", "--json", flag, "2026-11-17T15:00:00" + suffix)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    expected = {
        ("--since", False): (3, 135), ("--since", True): (2, 90),
        ("--until", False): (3, 820), ("--until", True): (4, 865),
    }[flag, suffix.startswith(".")]
    summary = json.loads(result.stdout)
    assert (summary["request_count"], summary["total_bytes"]) == expected
    assert summary["malformed_count"] == 1


@pytest.mark.parametrize("flag", ["--since", "--until"])
@pytest.mark.parametrize("value", [
    "20261117T150000Z", "2026-11-17 15:00:00Z", "2026-11-17t15:00:00z",
    "2026-02-30T15:00:00Z", "2026-11-17T15:00:00", "2026-11-17T15:00:00+01:00",
    "2026-11-17T15:00:00.1234567Z",
])
def test_invalid_timestamps_are_argument_errors(flag, value):
    assert_argument_error(flag, value)


@pytest.mark.parametrize("flag,value", [
    ("--status", "99"), ("--status", "600"), ("--status", "2XX"), ("--status", "200,404"),
    ("--top", "-1"), ("--top", "1.5"),
    ("--format", "xml"), ("--format", "json"),
])
def test_invalid_status_top_and_format_are_argument_errors(flag, value):
    assert_argument_error(flag, value)


@pytest.mark.parametrize("output_format", ["text", "csv"])
@pytest.mark.parametrize("json_first", [False, True])
def test_json_and_explicit_format_are_mutually_exclusive(output_format, json_first):
    args = ["--format", output_format]
    args = ["--json", *args] if json_first else [*args, "--json"]
    result = run_cli("sample/access.log", *args)

    assert result.returncode == 2
    assert result.stdout == ""
    assert "usage:" in result.stderr.lower()
    assert "--json" in result.stderr and "--format" in result.stderr
    assert "unrecognized arguments" not in result.stderr.lower()
    assert "traceback" not in result.stderr.lower()


def assert_argument_error(flag, value):
    result = run_cli("sample/access.log", flag, value)

    assert result.returncode == 2
    assert result.stdout == ""
    assert "usage:" in result.stderr.lower()
    assert flag in result.stderr
    assert "error:" in result.stderr.lower()
    assert "unrecognized arguments" not in result.stderr.lower()
    assert "traceback" not in result.stderr.lower()


def test_reversed_time_window_is_an_argument_error():
    result = run_cli("sample/access.log", "--since", "2026-11-17T15:00:01Z",
                     "--until", "2026-11-17T15:00:00Z")

    assert result.returncode == 2
    assert result.stdout == ""
    assert "since" in result.stderr and "until" in result.stderr
    assert "unrecognized arguments" not in result.stderr.lower()
    assert "traceback" not in result.stderr.lower()


def test_help_describes_filter_options():
    result = run_cli("--help")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    for flag in ("--since", "--until", "--status", "--top"):
        assert flag in result.stdout
