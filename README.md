# logstat

Summarize web-server access logs in Common Log Format using Python 3.11 or newer.
The command uses only the Python standard library and runs locally.

## Setup

Clone or download this repository, then open its directory. No package installation
is needed to run logstat. An optional virtual environment selects your Python:

```sh
python3 -m venv .venv
source .venv/bin/activate
python --version  # Must be 3.11 or newer.
```

Run commands from the repository root. The input is a file path; quote paths that
contain spaces.

## Usage

```sh
python -m logstat sample/access.log
python -m logstat sample/access.log --json
python -m logstat --help
```

The included sample has six valid requests and one malformed line. The default
command prints:

```text
Requests: 6
Malformed lines: 1
Total bytes: 955
Status codes:
  200: 3
  201: 1
  204: 1
  404: 1
Top paths:
  /products: 2
  /: 1
  /checkout: 1
  /health: 1
  /missing: 1
Requests per hour (UTC):
  2026-11-17T14:00:00Z: 3
  2026-11-17T15:00:00Z: 3
```

`--json` returns the same metrics in one JSON object:

| Key | Value |
| --- | --- |
| `request_count` | Number of valid records matching the filters |
| `malformed_count` | Number of malformed records in the entire input |
| `total_bytes` | Sum of matching records' response sizes |
| `status_counts` | Object mapping status-code strings to counts |
| `top_paths` | Up to `--top N` objects with `path` and `count` (default 10) |
| `requests_per_hour` | Object mapping UTC hour strings to counts |

## Filters

Filters work in both text and `--json` output. Without them, all valid requests
are counted as before.

| Option | Meaning |
| --- | --- |
| `--since UTC` | Include requests at or after this UTC timestamp |
| `--until UTC` | Include requests strictly before this UTC timestamp |
| `--status CODE` | Include one status code, from `100` through `599` |
| `--status CLASS` | Include a lowercase class such as `2xx` or `5xx` (`1xx`–`5xx`) |
| `--top N` | Show at most N paths; default 10, with 0 hiding paths only |

Use timestamps like `2026-11-17T15:00:00Z` or
`2026-11-17T15:00:00+00:00`. Seconds are required; an optional decimal fraction
has 1–6 digits, for example `2026-11-17T15:00:00.000001Z`. Invalid dates, missing
or non-UTC offsets, and longer fractions are argument errors. `--since` later
than `--until` is also an error; equal bounds successfully match no requests.

Repeat `--status` to include the union of selectors, without double counting.
For example, `--status 404 --status 5xx` includes 404 and all 500–599 responses.
Comma-separated selectors and uppercase classes are invalid. `--top` must be a
nonnegative integer; it changes only the path list, never the other metrics.

These examples use the included sample log:

```sh
python -m logstat sample/access.log --since 2026-11-17T15:00:00Z
# 3 requests, 135 bytes
python -m logstat sample/access.log --until 2026-11-17T15:00:00Z
# 3 requests, 820 bytes
python -m logstat sample/access.log --status 404
# 1 request, 45 bytes
python -m logstat sample/access.log --status 2xx --top 2
# 5 requests, 910 bytes; paths /products (2) and / (1)
```

Different filters combine by intersection. This example reports one request,
90 bytes, and `/checkout` as its only path:

```sh
python -m logstat sample/access.log --json \
  --since 2026-11-17T15:00:00Z --until 2026-11-17T15:30:00Z \
  --status 2xx --top 1
```

Every example still reports one malformed line. Malformed counts cover the
entire input; valid records excluded by filters are not malformed.

## Counting rules

- Each valid Common Log Format line matching the filters contributes one request.
  Blank lines, invalid dates or fields, and damaged UTF-8 records count as malformed
  and are skipped. Combined Log Format's extra fields are not supported.
- A byte size of `-` contributes zero. Individual integer fields exceeding
  Python's configured conversion limit are malformed; sums of accepted values
  retain their exact value in both output formats.
- Query strings are removed from request targets. Case, percent encoding and
  escaped characters are retained. Paths are ordered by count descending, then
  alphabetically for ties; `--top` limits the displayed paths (default ten).
- Timestamps are converted to UTC before grouping into hours. Status codes and
  hours are ordered ascending. Status codes must be in the range 100–599.
- Empty input succeeds with zero totals and empty collections. Text output uses
  `(none)` for empty sections. Malformed records alone do not cause a failing exit.
- File errors exit with status 1 and a message on stderr. Argument errors exit
  with status 2 and usage guidance. Successful summaries exit with status 0.

## Tests

Pytest is the only test dependency. With the virtual environment active:

```sh
python -m pip install pytest
pytest
```

The tests cover malformed inputs, deterministic aggregation, time-zone conversion,
exact time boundaries, status unions, path limits, the real command's text and
JSON output, and file/argument errors.

## Code and maintenance

`logstat/core.py` validates each record, converts timestamps to UTC, filters exact
instants and statuses, then aggregates the metrics. Its function
`summarize(lines, *, since=None, until=None, statuses=None, top=10)` accepts an
iterable of strings and returns the dictionary described above. API time bounds
are timezone-aware `datetime` objects (any offset); naive or reversed bounds
raise `ValueError`. `statuses` is a set of code strings: `None` includes all,
while an empty set matches none. `top` is a nonnegative integer.
`logstat/__main__.py` validates CLI arguments, reads the file, calls that function,
and renders text or JSON from the same result.

For a counting problem, reduce the input to the relevant lines and compare
`--json` output with the counting rules. For an invocation problem, check the
Python version, working directory and input path. Tests for the two layers live
in `tests/test_core.py` and `tests/test_cli.py`.

There is no service to deploy or persistent application state. Run the checkout
with a supported interpreter; to roll back, use an earlier known-good checkout.
Memory grows with the number of distinct paths, statuses and hours in the input.
