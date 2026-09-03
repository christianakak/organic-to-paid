"""Verify what gets sent to the paid stage, and what doesn't.

No API calls. This is about selection, filtering and bookkeeping — the
three things that decide how much a run costs and whether its output can
be ranked at all.

The bug that prompted this file: the documented first run is
`claims --limit 200`, and the cap used to be `rows[:200]` over a query
with no ORDER BY. Rows arrive in pull order, so a capped run saw one
source and nothing else. Source diversity is the highest-weighted term in
the scoring, so every claim from such a run would share a single source,
every diversity score would be identical, and the angle bank would look
completely plausible while being ranked on nothing.
"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ["ANGLE_DB"] = "/tmp/claims_sel.db"
if os.path.exists("/tmp/claims_sel.db"):
    os.remove("/tmp/claims_sel.db")

import importlib
import config; importlib.reload(config)
import db; importlib.reload(db)

db.init()
from pipeline import claims

ACC = "testco"

# A realistic lopsided corpus: mostly Meta comments, a handful of
# everything else. This is the shape that breaks naive slicing.
CORPUS = [
    ("meta_ig", "comment", 180),
    ("meta_page", "comment", 60),
    ("gsc", "query", 25),
    ("transcript", "comment", 20),
    ("gmail", "comment", 10),
    ("trustpilot", "comment", 5),
]

with db.connect() as conn:
    for source, kind, n in CORPUS:
        for i in range(n):
            db.insert_signal(
                conn, ACC, source, kind,
                f"kunden sa noe om {source} nummer {i} og lurte på passform",
                external_id=f"{source}-{i}",
            )
    conn.commit()

TOTAL = sum(n for _, _, n in CORPUS)


def sources_of(rows):
    out = {}
    for r in rows:
        out[r["source"]] = out.get(r["source"], 0) + 1
    return out


# ------------------------------------------------------------------
# 1. A capped run must span sources
# ------------------------------------------------------------------

with db.connect() as conn:
    sample = claims.select_signals(conn, ACC, limit=20)

mix = sources_of(sample)
assert len(sample) == 20, f"expected 20 rows, got {len(sample)}"
assert len(mix) == len(CORPUS), (
    f"a 20-row cap over 6 sources reached only {len(mix)}: {mix}. "
    f"Single-source samples make every diversity score identical."
)
print(f"PASS: a 20-row cap reaches all {len(mix)} sources — {mix}")

# The direction that actually matters. Reproduce the old behaviour and
# confirm this test would have caught it.
with db.connect() as conn:
    naive = conn.execute(
        "SELECT id, kind, source, text, raw FROM signal WHERE account = ?",
        (ACC,),
    ).fetchall()[:20]
assert len(sources_of(naive)) == 1, (
    "the old rows[:limit] behaviour should be single-source on this "
    "corpus — if it isn't, this test proves nothing"
)
print(f"PASS: the old slice was single-source ({list(sources_of(naive))}), "
      f"so the fix is doing real work")


# ------------------------------------------------------------------
# 2. Proportional, not equal
# ------------------------------------------------------------------
#
# Every source appearing is necessary but not sufficient. An even split
# would over-represent tiny sources and make the sample lie in the
# opposite direction.

with db.connect() as conn:
    sample = claims.select_signals(conn, ACC, limit=150)

mix = sources_of(sample)
assert mix["meta_ig"] > mix["trustpilot"] * 3, (
    f"a corpus that is 60% meta_ig should sample mostly meta_ig: {mix}"
)
share = mix["meta_ig"] / len(sample)
corpus_share = 180 / TOTAL
assert abs(share - corpus_share) < 0.20, (
    f"meta_ig is {corpus_share:.0%} of the corpus but {share:.0%} of the "
    f"sample"
)
print(f"PASS: the sample mix tracks the corpus mix "
      f"({share:.0%} vs {corpus_share:.0%} meta_ig)")

with db.connect() as conn:
    everything = claims.select_signals(conn, ACC, limit=None)
assert len(everything) == TOTAL, \
    f"an uncapped run must return everything: {len(everything)} of {TOTAL}"
print("PASS: an uncapped run returns the whole corpus")


# ------------------------------------------------------------------
# 3. The pre-filter, in the direction that matters
# ------------------------------------------------------------------
#
# Dropping junk saves money. Dropping a real objection corrupts the
# corpus and nothing reports it, so the keep cases carry more weight
# than the drop cases.

KEEP = [
    "for dyrt",                       # two words, a complete objection
    "passer den under sofaen?",
    "Er den vaskbar med hund",
    "kom raskt, veldig fornøyd",
]
DROP = [
    "👍👍👍",
    "!!!",
    "Takk",
    "@ola #interiør",
    "   ",
    "2026",
    # Long enough to clear the length rule, so only the word-count rule
    # can catch these. Without them, removing that rule broke nothing and
    # the sabotage run exited zero — a guard nothing was testing.
    "Kjempebra",
    "Supert!!!!!!",
    "FANTASTISK",
]

for text in KEEP:
    assert claims.is_extractable(text), \
        f"the filter would have eaten a real claim: {text!r}"
print(f"PASS: all {len(KEEP)} real claims survive the filter, "
      f"including two-word objections")

for text in DROP:
    assert not claims.is_extractable(text), \
        f"the filter passed something worth no money: {text!r}"
print(f"PASS: all {len(DROP)} junk cases are dropped before the API")

# The single-word rule is about comments. A one-word search query, or a
# page titled "Størrelsesguide", is not praise — it is unambiguous
# evidence of a concern, and it comes from the sources that make an
# angle cross-source in the first place. Dropping those would bias the
# corpus toward comments, which is the exact failure the diversity
# weighting exists to prevent.
for kind in ("query", "page"):
    assert claims.is_extractable("Størrelsesguide", kind), \
        f"a one-word {kind} is signal, not praise"
    assert claims.is_extractable("passform", kind)
assert not claims.is_extractable("Størrelsesguide", "comment"), \
    "the single-word rule must still apply to comments"
assert not claims.is_extractable("👍👍👍👍👍", "query"), \
    "emoji are junk whatever the kind"
print("PASS: one-word queries and page titles survive; one-word comments "
      "do not")

with db.connect() as conn:
    db.insert_signal(conn, ACC, "meta_ig", "comment", "🔥🔥🔥🔥🔥🔥",
                     external_id="junk-1")
    db.insert_signal(conn, ACC, "meta_ig", "comment", "Supert!!!!!!",
                     external_id="junk-2")
    conn.commit()
    picked = claims.select_signals(conn, ACC, limit=None)
assert not any("🔥" in r["text"] for r in picked), \
    "junk reached the work list despite the filter"
print("PASS: filtered rows never reach the batch builder")


# ------------------------------------------------------------------
# 4. Never pay twice for a signal that yields nothing
# ------------------------------------------------------------------
#
# Selection used to be "signals with no claim rows", so anything that
# correctly produced zero claims came back on every future run, forever.

with db.connect() as conn:
    first = claims.select_signals(conn, ACC, limit=None)
    assert first, "no work found at all"

    # Simulate a successful batch that legitimately found nothing in
    # half of it: mark everything processed, write claims for only some.
    for r in first[:5]:
        conn.execute(
            "INSERT INTO claim (account, signal_id, text, verbatim, "
            "claim_type) VALUES (?, ?, ?, ?, ?)",
            (ACC, r["id"], "passform er usikker", "passer den?", "objection"),
        )
    conn.executemany(
        "UPDATE signal SET claimed_at = ? WHERE id = ?",
        [("2026-09-03T12:00:00Z", r["id"]) for r in first],
    )
    conn.commit()

    second = claims.select_signals(conn, ACC, limit=None)

assert not second, (
    f"{len(second)} signals came back for a second paid pass despite "
    f"having been processed — including the ones that correctly yielded "
    f"no claims"
)
print("PASS: processed signals never return, even when they produced "
      "no claims")

# And the other direction: a signal the API never saw must still be there.
with db.connect() as conn:
    db.insert_signal(conn, ACC, "gmail", "comment",
                     "jeg lurte på om frakten er inkludert",
                     external_id="new-after-run")
    conn.commit()
    third = claims.select_signals(conn, ACC, limit=None)
assert len(third) == 1 and third[0]["text"].startswith("jeg lurte"), \
    f"new signals must be picked up: {[r['text'] for r in third]}"
print("PASS: signals arriving after a run are picked up by the next one")

print("\nall claim-selection checks passed")
