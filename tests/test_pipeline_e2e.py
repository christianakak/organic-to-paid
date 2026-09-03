"""Run the whole pipeline once, with the model stubbed.

Four functions in this project had never executed: claim extraction with
an actual response, clustering, brief writing, and the markdown render.
Every unit test around them exercised their inputs or their neighbours.
Nothing had ever put a signal in one end and read a filming brief out of
the other.

That is the gap this closes. The model is replaced with one that returns
realistic Norwegian JSON in the documented shape, so what is being tested
is the plumbing — the seams between stages, the SQL, the JSON contracts —
rather than the model's judgment, which no test can check anyway.

The stub also returns two things a real model will eventually return and
the code must survive: a response that is not JSON, and a batch where
some items yield no claims at all.
"""
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ["ANGLE_DB"] = "/tmp/e2e.db"
os.environ["ANGLE_OUT_DIR"] = "/tmp/e2e_out"
if os.path.exists("/tmp/e2e.db"):
    os.remove("/tmp/e2e.db")

import importlib
import config; importlib.reload(config)
import db; importlib.reload(db)

db.init()
from pipeline import brief, claims, render_html, score

ACC = "bty"


# ------------------------------------------------------------------
# A corpus that should produce one obvious winner
# ------------------------------------------------------------------
#
# The sizing worry recurs across five sources. Everything else sits in
# one or two. If the pipeline works, the cross-source objection ranks
# first — which is the entire thesis of the product, expressed as a
# fixture.

CORPUS = [
    ("meta_ig", "post", "Nytt teppe i butikken denne uka, kom innom",
     {"reach": 4200, "saves": 210, "shares": 30}),
    ("meta_ig", "post", "Slik måler du rommet før du bestiller",
     {"reach": 3100, "saves": 180, "shares": 22}),
    ("meta_ig", "comment",
     "Veldig fin, men jeg aner ikke om den passer i stua vår", {}),
    ("meta_ig", "comment",
     "Hvor stor er den egentlig? Bildene lyver litt synes jeg", {}),
    ("meta_page", "comment",
     "Er den for stor til en liten stue tror dere?", {}),
    ("meta_page", "comment",
     "Leveringen tok tre uker, det var lenge", {}),
    ("gsc", "query", "teppe stue mål hvor stor", {"impressions": 890}),
    ("gsc", "query", "hvor lang leveringstid teppe", {"impressions": 210}),
    ("transcript", "comment",
     "Jeg var redd for at den skulle bli altfor stor for rommet", {}),
    ("transcript", "comment",
     "Prisen var greit nok, det var størrelsen jeg lurte på", {}),
    ("trustpilot", "comment",
     "Nydelig kvalitet, men jeg måtte returnere fordi den ikke passet", {}),
    ("gmail", "comment",
     "Lurte på om dere har målene et sted, redd for at den blir feil", {}),
    ("ga4", "page", "Størrelsesguide", {"impressions": 1400,
                                        "conversions": 62}),
    # Extractable — long enough, several words — but carrying no
    # customer belief. The model returns zero claims for it, and the code
    # must mark it processed anyway or it comes back on every future run.
    ("meta_ig", "comment", "Sendte dere melding på DM i går?", {}),
]

with db.connect() as conn:
    for i, (source, kind, text, extra) in enumerate(CORPUS):
        db.insert_signal(conn, ACC, source, kind, text,
                         external_id=f"e2e-{i}", **extra)
    conn.commit()


# ------------------------------------------------------------------
# The stub
# ------------------------------------------------------------------

SIZING = "Jeg er redd for at den ikke passer i rommet mitt"
DELIVERY = "Leveringen tar lengre tid enn jeg forventet"

# Which canonical each fixture sentence should end up under. Keyed on a
# fragment rather than the whole string so the fixture stays readable.
CANON = [
    ("passer i stua", SIZING, "objection"),
    ("Hvor stor er den", SIZING, "objection"),
    ("for stor til en liten stue", SIZING, "objection"),
    ("altfor stor for rommet", SIZING, "objection"),
    ("ikke passet", SIZING, "objection"),
    ("blir feil", SIZING, "objection"),
    ("mål hvor stor", SIZING, "objection"),
    ("Størrelsesguide", SIZING, "objection"),
    ("tok tre uker", DELIVERY, "objection"),
    ("lang leveringstid", DELIVERY, "objection"),
    ("Slik måler du", "Det er lett å måle opp selv", "benefit"),
    ("Nytt teppe", "Butikken har nye varer", "benefit"),
    ("Prisen var greit", "Prisen er akseptabel", "proof"),
]

calls = {"extract": 0, "cluster": 0, "brief": 0}


def _canonical_for(text):
    for fragment, canonical, ctype in CANON:
        if fragment.lower() in text.lower():
            return canonical, ctype
    return None, None


class _Text:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Usage:
    input_tokens = 1200
    output_tokens = 300


class _Msg:
    def __init__(self, text):
        self.content = [_Text(text)]
        self.usage = _Usage()


class _Messages:
    def create(self, model, max_tokens, system, messages, **kw):
        payload = messages[0]["content"]

        if "extract advertising-relevant claims" in system:
            return _Msg(self._extract(payload))
        if "group customer claims" in system:
            return _Msg(self._cluster(payload))
        if "filming briefs" in system:
            return _Msg(self._brief(payload))
        raise AssertionError(f"unrecognised system prompt: {system[:60]}")

    def _extract(self, payload):
        calls["extract"] += 1
        items = json.loads(payload)
        out = []
        for item in items:
            canonical, ctype = _canonical_for(item["text"])
            if not canonical:
                # A real batch contains items that carry no claim. The
                # code must mark these processed rather than retrying
                # them forever.
                out.append({"i": item["i"], "claims": []})
                continue
            out.append({"i": item["i"], "claims": [{
                "text": canonical,
                "verbatim": item["text"][:90],
                "claim_type": ctype,
            }]})
        # Fenced JSON, because models do this and _json_from is supposed
        # to tolerate it.
        return "```json\n" + json.dumps(out, ensure_ascii=False) + "\n```"

    def _cluster(self, payload):
        calls["cluster"] += 1
        data = json.loads(payload)
        out = []
        for item in data["new"]:
            canonical, ctype = _canonical_for(item["text"])
            out.append({
                "i": item["i"],
                "canonical": canonical or item["text"],
                "claim_type": ctype or item["claim_type"],
            })
        return json.dumps(out, ensure_ascii=False)

    def _brief(self, payload):
        calls["brief"] += 1
        angle = json.loads(payload)
        return json.dumps({
            "hook": f"Alle spør om det samme: {angle['angle'].lower()}",
            "script": "Her er rommet. Her er målebåndet.\n\nDet er hele "
                      "videoen.",
            "scene": "Vanlig stue, dagslys, sofaen synlig hele veien.",
            "talent": "Én person, snakker til kamera som til en venn.",
            "props": "Målebånd, produktet, sofaen som allerede står der.",
            "rationale": f"{angle['times_mentioned']} omtaler på tvers av "
                         f"{len(angle['appears_in_sources'])} kilder.",
            "testing": "Om det å nevne bekymringen først slår å vise "
                       "produktet i et pent rom.",
        }, ensure_ascii=False)


class _Client:
    messages = _Messages()


claims._client = _Client()
claims.client = lambda: _Client()


# ------------------------------------------------------------------
# 1. Extraction
# ------------------------------------------------------------------

with db.connect() as conn:
    n = claims.extract_claims(conn, ACC, batch_size=5)

assert n > 0, "extraction produced no claims at all"
assert calls["extract"] >= 2, "batching did not happen"

with db.connect() as conn:
    unprocessed = conn.execute(
        "SELECT COUNT(*) AS n FROM signal WHERE account = ? "
        "AND claimed_at IS NULL", (ACC,)).fetchone()["n"]
    with_claims = conn.execute(
        "SELECT COUNT(DISTINCT signal_id) AS n FROM claim "
        "WHERE account = ?", (ACC,)).fetchone()["n"]

assert unprocessed == 0, f"{unprocessed} signals left unmarked after a " \
                         f"clean run"
assert with_claims < len(CORPUS), \
    "every signal produced a claim — the fixture's empty case never ran"
print(f"PASS: {n} claims from {len(CORPUS)} signals, "
      f"{len(CORPUS) - with_claims} correctly yielded nothing")

# Re-running must cost nothing. This is the idempotence that stops the
# only paid stage charging twice for the same corpus.
before = calls["extract"]
with db.connect() as conn:
    again = claims.extract_claims(conn, ACC, batch_size=5)
assert again == 0 and calls["extract"] == before, \
    f"a second run made {calls['extract'] - before} more API calls"
print("PASS: re-running extraction makes zero API calls")


# ------------------------------------------------------------------
# 2. Clustering
# ------------------------------------------------------------------

with db.connect() as conn:
    k = claims.cluster_claims(conn, ACC, batch_size=6)
    clusters = conn.execute(
        "SELECT id, canonical, claim_type FROM cluster WHERE account = ?",
        (ACC,)).fetchall()
    orphans = conn.execute(
        "SELECT COUNT(*) AS n FROM claim WHERE account = ? "
        "AND cluster_id IS NULL", (ACC,)).fetchone()["n"]

assert orphans == 0, f"{orphans} claims never got a cluster"
canonicals = {c["canonical"] for c in clusters}
assert SIZING in canonicals, f"the sizing angle did not survive: {canonicals}"
print(f"PASS: {len(clusters)} canonical angles, no orphaned claims")

# The property that makes batched clustering work at all: batch two must
# reuse batch one's canonicals rather than inventing parallel ones.
assert calls["cluster"] >= 2, "clustering ran in one batch, proving nothing"
sizing_clusters = [c for c in clusters if c["canonical"] == SIZING]
assert len(sizing_clusters) == 1, \
    f"the same canonical was created {len(sizing_clusters)} times across " \
    f"batches — canonicals are not carrying forward"
print("PASS: canonicals carry across batches instead of duplicating")


# ------------------------------------------------------------------
# 3. Scoring — the thesis, as an assertion
# ------------------------------------------------------------------

with db.connect() as conn:
    angles = score.score_account(conn, ACC)

assert angles, "scoring returned nothing"
top = angles[0]
assert top["canonical"] == SIZING, (
    f"the cross-source objection did not rank first — got "
    f"{top['canonical']!r} with sources {top['sources']}"
)
assert len(top["sources"]) >= 5, (
    f"the winning angle should span most sources, spans "
    f"{top['sources']}"
)
print(f"PASS: the angle appearing in {len(top['sources'])} sources ranks "
      f"first — {top['canonical']}")

delivery = [a for a in angles if a["canonical"] == DELIVERY][0]
assert top["breakdown"]["diversity"] > delivery["breakdown"]["diversity"], \
    "diversity is not separating a five-source claim from a two-source one"
print("PASS: source diversity separates the two objections")


# ------------------------------------------------------------------
# 4. Briefs and both renders
# ------------------------------------------------------------------

brief.client = lambda: _Client()

with db.connect() as conn:
    top3 = angles[:3]
    briefs = [brief.write_brief(conn, ACC, a, "Norsk interiørbutikk")
              for a in top3]
    stored = conn.execute(
        "SELECT COUNT(*) AS n FROM brief").fetchone()["n"]

assert stored == 3, f"{stored} briefs stored, expected 3"
assert all(b.get("hook") for b in briefs), "a brief came back without a hook"
assert briefs[0]["quotes"], "the winning angle has no evidence quotes"
print(f"PASS: {stored} briefs written and stored, with verbatim evidence "
      f"attached")

md = brief.render_markdown(ACC, top3, briefs)
assert SIZING in md and "Filming briefs" in md
assert briefs[0]["quotes"][0]["text"] in md, \
    "the customer's own words did not reach the markdown"
print(f"PASS: markdown renders, {len(md.splitlines())} lines")

page = render_html.render_html(ACC, angles, briefs)
assert SIZING in page and "Instagram" in page, \
    "the HTML deliverable is missing content or still shows raw slugs"
assert "meta_ig" not in page, "raw adapter slugs leaked into the deliverable"
print(f"PASS: HTML deliverable renders, {len(page):,} bytes")

# Writing the files is its own seam — directory creation and encoding
# both have to work, and Norwegian text makes the encoding non-academic.
config.OUT_DIR.mkdir(parents=True, exist_ok=True)
md_path = config.OUT_DIR / f"{ACC}-angle-bank.md"
html_path = config.OUT_DIR / f"{ACC}-angle-bank.html"
md_path.write_text(md, encoding="utf-8")
html_path.write_text(page, encoding="utf-8")

for path in (md_path, html_path):
    written = path.read_text(encoding="utf-8")
    assert SIZING in written, f"the winning angle is missing from {path.name}"
    assert "redd for at den ikke passer" in written, \
        f"Norwegian characters did not survive the round-trip in {path.name}"
    assert "Ã¸" not in written and "�" not in written, \
        f"mojibake in {path.name} — something wrote it as latin-1"
print(f"PASS: both deliverables written to {config.OUT_DIR}, "
      f"Norwegian intact")


# ------------------------------------------------------------------
# 5. A response that is not JSON must not lose signal
# ------------------------------------------------------------------

with db.connect() as conn:
    db.insert_signal(conn, ACC, "gmail", "comment",
                     "Jeg lurte på om frakten er inkludert i prisen",
                     external_id="e2e-late")
    conn.commit()

_Messages._extract = lambda self, payload: "I'm sorry, I can't do that."

with db.connect() as conn:
    got = claims.extract_claims(conn, ACC, batch_size=5)
    still_pending = conn.execute(
        "SELECT COUNT(*) AS n FROM signal WHERE account = ? "
        "AND claimed_at IS NULL", (ACC,)).fetchone()["n"]
    noted = conn.execute(
        "SELECT detail FROM pull_log WHERE source = 'claims' "
        "ORDER BY id DESC LIMIT 1").fetchone()

assert got == 0
assert still_pending == 1, (
    "an unparseable response marked the signal processed — it looks "
    "identical to 'no claims here' and would silently discard real signal"
)
assert noted and "not JSON" in noted["detail"], \
    "an unparseable response was not recorded anywhere"
print("PASS: an unparseable response leaves signals for a later run "
      "and says so")

print("\nfull pipeline ran end to end")
