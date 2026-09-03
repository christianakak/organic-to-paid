"""Verify the HappyScribe request shapes, with HTTP stubbed.

Three parameters in this adapter are load-bearing and none of them are
visible in the output when they are wrong:

`show_speakers` — without it the export has no speaker labels, the
transcript parses as one undifferentiated blob, and the sales rep's
pitch and the customer's objection become one voice. Everything still
runs. Nothing reports a problem. Transcripts just stop being the best
source in the corpus and become the worst.

`per_page` — the API defaults it to 5. A pull that forgets it looks
exactly like an account with five transcripts in it.

`organization_id` — required, not defaulted. Omitting it fails the whole
listing.

Exports are also asynchronous: create, poll, download. A stub that
returned `ready` immediately would let a broken polling loop pass, so
the fake here stays pending for a turn first.

No network. Shapes verified against dev.happyscribe.com on 2026-09-03;
this file locks in what was read there.
"""
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ["ANGLE_DB"] = "/tmp/hs_test.db"
if os.path.exists("/tmp/hs_test.db"):
    os.remove("/tmp/hs_test.db")

import importlib
import config; importlib.reload(config)
import db; importlib.reload(db)

db.init()
import auth
from sources import happyscribe

ACC = "bty"
happyscribe.EXPORT_POLL = 0        # don't actually sleep

sent = {"requests": [], "downloads": 0}

TRANSCRIPT = """Selger: Takk for at du tok deg tid i dag.
Kunde: Ingen problem. Jeg har sett på disse en stund.
Selger: Hva har holdt deg tilbake?
Kunde: Ærlig talt var jeg redd for at den ikke ville passe i stua vår.
"""


class _Resp:
    def __init__(self, payload, status=200, text=None):
        self._payload = payload
        self.status_code = status
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"status {self.status_code}")


def fake_request(method, url, headers=None, timeout=None, **kw):
    sent["requests"].append({"method": method, "url": url, **kw})
    path = url.split("/api/v1/")[-1]

    if path == "organizations":
        return _Resp([{"id": "org_42", "name": "BTY"}])

    if path == "transcriptions":
        params = kw.get("params", {})
        page = params.get("page", 0)
        if page > 0:
            return _Resp({"results": []})
        return _Resp({"results": [
            {"id": "t1", "state": "automatic_done",
             "createdAt": "2026-08-01T10:00:00Z"},
        ]})

    if path == "exports":
        return _Resp({"id": "exp_1", "state": "pending"}, status=201)

    if path.startswith("exports/"):
        # Pending the first time, ready after. A stub that was ready
        # immediately would let a broken polling loop pass.
        prior = sum(1 for r in sent["requests"]
                    if r["url"].endswith("exports/exp_1"))
        if prior <= 1:
            return _Resp({"id": "exp_1", "state": "pending"})
        return _Resp({"id": "exp_1", "state": "ready",
                      "download_link": "https://files.example/exp_1.txt"})

    raise AssertionError(f"unexpected request to {path}")


def fake_get(url, timeout=None, **kw):
    if url.startswith("https://files.example/"):
        sent["downloads"] += 1
        return _Resp(None, text=TRANSCRIPT)
    return fake_request("GET", url, **kw)


happyscribe.requests.request = fake_request
happyscribe.requests.get = fake_get

with db.connect() as conn:
    auth.save_connection(conn, ACC, "happyscribe", access_token="k")
    auth.update_settings(conn, ACC, "happyscribe", api_key="k",
                         folder_id="f1")
    conn.commit()


# ------------------------------------------------------------------
# 1. Verification uses an endpoint that works without an org id
# ------------------------------------------------------------------

assert happyscribe.verify("k") is True
last = sent["requests"][-1]
assert last["url"].endswith("/organizations"), (
    f"verify hit {last['url']} — listing transcriptions needs an "
    f"organization_id we do not have yet, so a good key would fail for "
    f"the wrong reason"
)
print("PASS: key verification uses /organizations, not /transcriptions")


# ------------------------------------------------------------------
# 2. The pull
# ------------------------------------------------------------------

sent["requests"].clear()
with db.connect() as conn:
    n = happyscribe.pull(conn, ACC)

assert n > 0, "no speaker turns were stored"
print(f"PASS: pull stored {n} speaker turns")

listing = [r for r in sent["requests"] if r["url"].endswith("transcriptions")]
assert listing, "never listed transcriptions"
params = listing[0]["params"]

assert params.get("organization_id") == "org_42", (
    f"organization_id missing or wrong: {params}. It is required, not "
    f"defaulted, and it was discovered rather than asked for."
)
print("PASS: organization_id is discovered and sent")

assert params.get("per_page", 0) > 5, (
    f"per_page is {params.get('per_page')} — the API defaults it to 5, "
    f"so an unset value looks like an account with five transcripts"
)
print(f"PASS: per_page set to {params['per_page']}, not left at the "
      f"default of 5")

assert params.get("folder_id") == "f1", "the chosen folder was not applied"
print("PASS: the chosen folder filters the listing")


# ------------------------------------------------------------------
# 3. The flag that decides whether transcripts are worth anything
# ------------------------------------------------------------------

exports = [r for r in sent["requests"]
           if r["url"].endswith("/exports") and r["method"] == "POST"]
assert exports, "no export was created"
body = exports[0]["json"]

assert body.get("show_speakers") is True, (
    f"show_speakers is {body.get('show_speakers')!r}. Without it the "
    f"export has no speaker labels, the transcript parses as one blob, "
    f"and the rep's pitch and the customer's objection become one voice "
    f"— silently."
)
assert body.get("format") == "txt"
assert body.get("transcription_ids") == ["t1"], \
    "transcription_ids must be a list, per the API"
print("PASS: the export requests speaker labels, which is what makes "
      "transcripts worth having")


# ------------------------------------------------------------------
# 4. Async export: created, polled while pending, then downloaded
# ------------------------------------------------------------------

polls = [r for r in sent["requests"] if "exports/exp_1" in r["url"]]
assert len(polls) >= 2, (
    f"the export was polled {len(polls)} time(s). It comes back pending "
    f"and only later becomes ready; a single GET would return nothing "
    f"usable."
)
assert sent["downloads"] == 1, \
    f"{sent['downloads']} downloads — the ready export must be fetched " \
    f"from its download_link"
print(f"PASS: export created, polled {len(polls)}x while pending, then "
      f"downloaded")


# ------------------------------------------------------------------
# 5. The turns actually split, and are scrubbed
# ------------------------------------------------------------------

with db.connect() as conn:
    rows = conn.execute(
        "SELECT text, raw FROM signal WHERE account = ? AND "
        "source = 'transcript' ORDER BY id", (ACC,)).fetchall()

assert len(rows) >= 3, (
    f"{len(rows)} turns from a four-turn transcript — the speaker split "
    f"did not happen"
)
speakers = {json.loads(r["raw"])["speaker"] for r in rows}
assert len(speakers) >= 2, (
    f"every turn was attributed to {speakers} — customer and rep are "
    f"not being told apart, which is the whole point of this source"
)
assert any("passe i stua" in r["text"] for r in rows), \
    "the customer's objection did not survive to the database"
print(f"PASS: {len(rows)} turns across {len(speakers)} speakers, "
      f"objection intact")


# ------------------------------------------------------------------
# 6. A blob is reported, not silently accepted
# ------------------------------------------------------------------

happyscribe.requests.get = lambda url, timeout=None, **kw: (
    _Resp(None, text="Vi snakket en stund og hovedsaken var om den "
                     "ville passe, og prisen, og leveringen.")
    if url.startswith("https://files.example/")
    else fake_request("GET", url, **kw)
)

with db.connect() as conn:
    conn.execute("DELETE FROM signal WHERE account = ?", (ACC,))
    conn.execute("DELETE FROM pull_log WHERE account = ?", (ACC,))
    conn.commit()
    happyscribe.pull(conn, ACC)
    noted = conn.execute(
        "SELECT detail FROM pull_log WHERE account = ? AND event = 'dropped'",
        (ACC,)).fetchall()

assert any("speaker labels" in d["detail"] for d in noted), (
    "a transcript with no speaker labels was ingested without comment. "
    "It still runs — that is exactly why it has to be reported."
)
print("PASS: an unsplit transcript is reported rather than quietly "
      "accepted")

print("\nall happyscribe checks passed")
