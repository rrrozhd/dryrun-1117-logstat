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


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "logstat", *map(str, args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )


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


def test_default_text_summary_matches_hand_counted_log(mixed_log):
    result = run_cli(mixed_log)

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


@pytest.mark.parametrize("contents,malformed", [("", 0), ("broken\n\n", 2)])
def test_empty_and_all_malformed_files_succeed(tmp_path, contents, malformed):
    path = tmp_path / "access.log"
    path.write_text(contents, encoding="utf-8")

    result = run_cli(path, "--json")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
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


def test_help_describes_input_and_json_option():
    result = run_cli("--help")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "usage:" in result.stdout.lower()
    assert "logfile" in result.stdout.lower()
    assert "--json" in result.stdout


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


@pytest.mark.parametrize("json_output", [False, True], ids=["text", "json"])
def test_large_accumulated_bytes_remain_exact(tmp_path, monkeypatch, json_output):
    monkeypatch.setenv("PYTHONINTMAXSTRDIGITS", "640")
    prefix = VALID_LINE.rsplit(" ", 1)[0] + " "
    valid = prefix + "9" * 640 + "\n"
    oversized = prefix + "9" * 641 + "\n"
    path = tmp_path / "large.log"
    path.write_text(valid * 2 + oversized, encoding="utf-8")

    result = run_cli(path, *(["--json"] if json_output else []))

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    expected_total = "1" + "9" * 639 + "8"
    if json_output:
        summary = json.loads(result.stdout, parse_int=str)
        assert summary["request_count"] == "2"
        assert summary["malformed_count"] == "1"
        assert summary["total_bytes"] == expected_total
    else:
        assert "Requests: 2\nMalformed lines: 1\n" in result.stdout
        assert f"Total bytes: {expected_total}\n" in result.stdout
