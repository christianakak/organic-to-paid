#!/usr/bin/env bash
#
# Run every test suite. Exits non-zero if any of them failed.
#
# This exists because the obvious one-liner does not work:
#
#     for t in tests/test_*.py; do python "$t" || break; done
#
# `break` exits 0. That loop reports success whether or not a test
# failed, and piping it to `tail` — which everyone does — reports the
# pager's status instead. It was in this project's own README and
# CLAUDE.md, presented as the way to verify the suite, and a red run and
# a green run produced the same exit code.
#
# Which is the failure this repo keeps finding in other people's guards
# and had shipped in its own.

set -u

cd "$(dirname "$0")" || exit 2

PY="${PY:-.venv/bin/python}"
if [ ! -x "$PY" ]; then
    echo "No interpreter at $PY"
    echo "  uv venv --python 3.12 && uv pip install -r requirements.txt"
    exit 2
fi

failed=0
total=0

for t in tests/test_*.py; do
    total=$((total + 1))
    printf '%-34s' "$t"
    if out=$("$PY" "$t" 2>&1); then
        echo "pass"
    else
        echo "FAIL"
        echo "$out" | tail -20 | sed 's/^/    /'
        failed=$((failed + 1))
    fi
done

echo
if [ "$failed" -eq 0 ]; then
    echo "$total suites passed"
    exit 0
fi

echo "$failed of $total suites FAILED"
exit 1
