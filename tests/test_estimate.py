"""Verify the cost estimate is derived from the corpus, not invented.

The failure this guards against is an estimator that always prints a
plausible number. A confident wrong estimate is worse than no estimate,
because it gets believed — the whole reason it exists is that `claims`
is the only stage that spends money and there was no way to know the
bill before committing to it.

So: the number must move when the corpus moves, the arithmetic must be
checkable by hand, and it must spend nothing to produce.
"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ["ANGLE_DB"] = "/tmp/estimate_test.db"
if os.path.exists("/tmp/estimate_test.db"):
    os.remove("/tmp/estimate_test.db")

import importlib
import config; importlib.reload(config)
import db; importlib.reload(db)

db.init()
from pipeline import estimate

ACC = "testco"

# The estimator's one API call is count_tokens, which is free but still
# a network call. Stubbed with something proportional to the payload, so
# the arithmetic below is checkable rather than magic.
calls = {"n": 0}


class _FakeCount:
    def __init__(self, n):
        self.input_tokens = n


class _FakeMessages:
    def count_tokens(self, model, system, messages):
        calls["n"] += 1
        chars = len(system) + sum(len(m["content"]) for m in messages)
        return _FakeCount(chars // 4)

    def create(self, **kw):
        raise AssertionError(
            "the estimator sent a real request — it must never spend money"
        )


class _FakeClient:
    messages = _FakeMessages()


estimate._client = lambda: _FakeClient()


def add(n, prefix="kunden"):
    with db.connect() as conn:
        for i in range(n):
            db.insert_signal(
                conn, ACC, "meta_ig", "comment",
                f"{prefix} lurte på om den passer under sofaen, nummer {i}",
                external_id=f"{prefix}-{i}",
            )
        conn.commit()


# ------------------------------------------------------------------
# 1. Nothing to do is said plainly, not priced at zero dollars
# ------------------------------------------------------------------

with db.connect() as conn:
    text = estimate.report(conn, ACC)
assert "Nothing to extract" in text, text
assert calls["n"] == 0, "an empty corpus should not be worth an API call"
print("PASS: an empty corpus reports nothing to do and calls nothing")


# ------------------------------------------------------------------
# 2. The number moves with the corpus
# ------------------------------------------------------------------

add(100)
with db.connect() as conn:
    small = estimate.count_batches(conn, ACC)

add(300, prefix="andre")
with db.connect() as conn:
    large = estimate.count_batches(conn, ACC)

assert large["signals"] == 400 and small["signals"] == 100
assert large["input_tokens"] > small["input_tokens"] * 3, (
    f"quadrupling the corpus barely moved the estimate: "
    f"{small['input_tokens']} -> {large['input_tokens']}. "
    f"An estimator reading a constant would pass everything else here."
)
print(f"PASS: 4x the corpus gives ~4x the tokens "
      f"({small['input_tokens']:,} -> {large['input_tokens']:,})")

assert large["batches"] == 16, f"400 signals / 25 = 16, got {large['batches']}"
assert large["sampled"] <= 6, "sampling more batches than needed costs time"
print(f"PASS: {large['batches']} batches, measured on {large['sampled']} "
      f"of them")


# ------------------------------------------------------------------
# 3. The arithmetic is checkable
# ------------------------------------------------------------------

usd = estimate.price(1_000_000, 100_000, "claude-opus-5")
assert abs(usd - (5.00 + 2.50)) < 1e-9, usd
usd = estimate.price(1_000_000, 100_000, "claude-sonnet-5")
assert abs(usd - (2.00 + 1.00)) < 1e-9, usd
print("PASS: pricing is input x rate + output x rate, per the rate card")

assert estimate.price(1, 1, "some-future-model") is None, \
    "an unknown model must return no price rather than a wrong one"
print("PASS: an unpriced model reports tokens rather than a wrong number")


# ------------------------------------------------------------------
# 4. The pre-filter's effect is visible, and the cap is honoured
# ------------------------------------------------------------------

# Long enough to clear the SQL length guard, so the local pre-filter is
# what has to catch them. Anything shorter never reaches it and would
# make this test pass for the wrong reason.
with db.connect() as conn:
    for i in range(25):
        db.insert_signal(conn, ACC, "meta_ig", "comment", "🔥🔥🔥🔥🔥🔥🔥🔥🔥🔥",
                         external_id=f"junk-a-{i}")
    for i in range(25):
        db.insert_signal(conn, ACC, "meta_ig", "comment", "Supert!!!!!!",
                         external_id=f"junk-b-{i}")
    conn.commit()
    raw, kept = estimate.unfiltered_count(conn, ACC)

assert raw == 450 and kept == 400, f"raw={raw} kept={kept}"
print(f"PASS: the report shows {raw - kept} rows dropped locally, for free")

with db.connect() as conn:
    capped = estimate.count_batches(conn, ACC, limit=50)
assert capped["signals"] == 50 and capped["batches"] == 2
assert capped["input_tokens"] < large["input_tokens"], \
    "a capped run must be cheaper than the full one"
print("PASS: --limit is priced as the capped run, not the full corpus")


# ------------------------------------------------------------------
# 5. It never spends money
# ------------------------------------------------------------------
#
# _FakeMessages.create raises. Reaching here means nothing tried to
# generate anything, which is the property that lets you run this
# freely while deciding.

with db.connect() as conn:
    report = estimate.report(conn, ACC)
assert "claude-opus-5" in report and "claude-sonnet-5" in report
assert "$" in report
print("PASS: the report prices every model and spends nothing")

print("\nall estimate checks passed")
