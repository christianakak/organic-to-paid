"""Verify the connect flows for the sources added after Meta and Google.

No network. The two calls that would hit an API — verifying a HappyScribe
key and resolving a Trustpilot domain — are replaced, so this tests the
wiring rather than the vendors.

The thing being held down: a bad credential must be rejected *and not
stored*. Storing a key that does not work turns an immediate, obvious
failure into a pull that silently returns nothing three steps later,
which is the failure mode this whole project keeps designing against.
"""
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ["ANGLE_DB"] = "/tmp/connect_test.db"
if os.path.exists("/tmp/connect_test.db"):
    os.remove("/tmp/connect_test.db")

import auth
import db
import onboard
import providers

db.init()
onboard.ACCOUNT = "acme"
c = onboard.app.test_client()

ACC = "acme"

# Connecting a source fires a background sync. Left alone it makes real
# API calls with the fake credentials this file uses — which it did, and
# the resulting 401 surfaced correctly in the drop panel, proving the
# error surfacing works but making the test depend on a network. The
# sync has its own coverage; this file is about the connect wiring.
onboard._start_sync = lambda account: None


# ------------------------------------------------------------------
# 1. The registry actually drives the generic route
# ------------------------------------------------------------------

assert c.get("/connect/happyscribe/key").status_code == 200
assert c.get("/connect/trustpilot/key").status_code == 200
assert c.get("/connect/nonsense/key").status_code == 404, \
    "an unknown source must 404, not render an empty form"
print("PASS: api_key route is driven by the registry, unknown sources 404")

# Meta and Google are OAuth and must not be reachable through the
# paste-a-key door — they have their own flows and their own pickers.
assert c.get("/connect/meta/key").status_code == 404
assert c.get("/connect/google/key").status_code == 404
print("PASS: OAuth sources are not exposed as api_key sources")

import html as html_mod

page = c.get("/connect/happyscribe/key").get_data(as_text=True)
spec = providers.get("happyscribe")
assert spec["credential"]["where"] in page, \
    "the form should tell you where to find the key, from the descriptor"
# Unescaped, because the descriptor contains an apostrophe and Jinja
# escapes it. Comparing raw strings here would pass only by accident.
assert spec["why"] in html_mod.unescape(page)
print("PASS: the form is rendered from the descriptor, not hardcoded")


# ------------------------------------------------------------------
# 2. A bad key is rejected and not stored
# ------------------------------------------------------------------

from sources import happyscribe

real_verify = happyscribe.verify


def reject(key):
    raise RuntimeError("HappyScribe 401: invalid token")


happyscribe.verify = reject
r = c.post("/connect/happyscribe/key", data={"api_key": "wrong"})
assert r.status_code == 400, f"a rejected key should not redirect: {r.status_code}"
assert "rejected" in r.get_data(as_text=True).lower()

with db.connect() as conn:
    assert not auth.get_settings(conn, ACC, "happyscribe").get("api_key"), \
        "a key that failed verification was stored anyway"
print("PASS: a rejected key is not written to the database")


# ------------------------------------------------------------------
# 3. A good key is stored, and leads to the picker
# ------------------------------------------------------------------

happyscribe.verify = lambda key: True
r = c.post("/connect/happyscribe/key", data={"api_key": "hs_good"})
assert r.status_code == 302 and "/configure" in r.headers["Location"], \
    f"a good key should lead to the folder picker: {r.headers.get('Location')}"

with db.connect() as conn:
    assert auth.get_settings(conn, ACC, "happyscribe")["api_key"] == "hs_good"
print("PASS: a verified key is stored and leads to the picker")

happyscribe.list_folders = lambda conn, account: [
    {"id": "f1", "name": "Salgssamtaler"},
    {"id": "f2", "name": "Support"},
]
html = c.get("/connect/happyscribe/configure").get_data(as_text=True)
assert "Salgssamtaler" in html and "Support" in html
print("PASS: folder picker lists what the API returned")

c.post("/connect/happyscribe/configure", data={"folder_id": "f1"})
with db.connect() as conn:
    settings = auth.get_settings(conn, ACC, "happyscribe")
assert settings["folder_id"] == "f1"
assert settings["api_key"] == "hs_good", \
    "writing the folder wiped the key — settings must merge, not replace"
print("PASS: settings merge rather than overwrite each other")


# ------------------------------------------------------------------
# 4. Trustpilot resolves a domain rather than asking for an id
# ------------------------------------------------------------------

c.post("/connect/trustpilot/key", data={"api_key": "tp_key"})

import sources.firstparty as fp

fp.resolve_business_unit = lambda key, domain: (
    ("46a1b2c3d4e5f60718293a4b", "BTY Gruppen")
    if domain == "btygruppen.no" else (None, f"no profile for {domain}")
)

r = c.post("/connect/trustpilot/configure", data={"domain": "nope.example"})
assert r.status_code == 400 and "no profile" in r.get_data(as_text=True), \
    "an unresolvable domain must say so rather than storing nothing quietly"
with db.connect() as conn:
    assert not auth.get_settings(conn, ACC, "trustpilot").get(
        "business_unit_id")
print("PASS: an unresolvable domain is reported and nothing is stored")

r = c.post("/connect/trustpilot/configure", data={"domain": "btygruppen.no"})
assert r.status_code == 302
with db.connect() as conn:
    settings = auth.get_settings(conn, ACC, "trustpilot")
assert settings["business_unit_id"] == "46a1b2c3d4e5f60718293a4b", \
    "the id you never have to see should have been looked up for you"
assert settings["display_name"] == "BTY Gruppen"
print("PASS: a domain resolves to a business unit id")


# ------------------------------------------------------------------
# 5. Gmail is never pulled unbounded
# ------------------------------------------------------------------

from sources import gmail

with db.connect() as conn:
    n = gmail.pull(conn, ACC)
    assert n == 0, "Gmail returned messages with no label chosen"
    logged = conn.execute(
        "SELECT detail FROM pull_log WHERE source = 'gmail' "
        "AND event = 'skipped' ORDER BY id DESC LIMIT 1"
    ).fetchone()
assert logged and "unbounded" in logged["detail"], \
    "skipping Gmail for want of a label must be recorded, not silent"
print("PASS: Gmail with no label reads nothing and says why")


# ------------------------------------------------------------------
# 6. The page reflects all of it
# ------------------------------------------------------------------

html = c.get("/").get_data(as_text=True)
assert "Gmail" in html and "Call transcripts" in html and "Reviews" in html
assert "BTY Gruppen" in html, "a configured source should show what it points at"
print("PASS: every source has a row and configured ones say what they point at")

status = json.loads(c.get("/status").get_data(as_text=True))
for key in ("gmail", "happyscribe", "trustpilot", "dropped"):
    assert key in status, f"/status is missing {key}"
print("PASS: /status carries the new sources")

# The drop panel shows things that went wrong during a real attempt. A
# `skipped` event means "not configured yet", which the source's own row
# already says — repeating it as a warning would train people to ignore
# the panel, which is the only way a panel like this fails.
assert not status["dropped"], \
    f"an unconfigured source should not raise a warning: {status['dropped']}"
print("PASS: 'not configured yet' does not show up as a failure")

with db.connect() as conn:
    db.record(conn, ACC, "meta_ig", "dropped",
              "media 123: API returned no saved — metric may have been renamed")
    conn.commit()

status = json.loads(c.get("/status").get_data(as_text=True))
assert any("renamed" in d["detail"] for d in status["dropped"]), \
    "a real drop must surface where someone can still act on it"
assert "renamed" in c.get("/").get_data(as_text=True)
print("PASS: a real drop surfaces on the page and in /status")

happyscribe.verify = real_verify
print("\nall connect checks passed")
