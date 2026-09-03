import sys, os
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
os.environ["ANGLE_DB"] = "/tmp/onb.db"
if os.path.exists("/tmp/onb.db"): os.remove("/tmp/onb.db")
import db, auth, onboard
db.init()
onboard.ACCOUNT = "acme"
c = onboard.app.test_client()

# 1. empty state
r = c.get("/")
assert r.status_code == 200, r.status_code
html = r.get_data(as_text=True)
assert "Connect Facebook to start." in html
assert "Someone else manages this" in html
print("PASS empty state renders, delegation offered upfront")

# 2. simulate meta connected + data pulled
with db.connect() as conn:
    auth.save_connection(conn, "acme", "meta", access_token="t",
                         meta_page_id="123", meta_ig_user_id="456")
    for i in range(3):
        db.insert_signal(conn, "acme", "meta_ig", "post", f"post {i}", external_id=f"p{i}")
    for i in range(28):
        db.insert_signal(conn, "acme", "meta_ig", "comment", f"comment {i}", external_id=f"c{i}")
    conn.commit()

r = c.get("/")
html = r.get_data(as_text=True)
assert ">31<" in html, "tally should total 31"
assert "28 comments" in html
assert "is-done" in html
assert "enough to work with" in html
print("PASS tally counts, meta row marked done, payoff shown before further asks")

# 3. status endpoint for polling
import json
s = json.loads(c.get("/status").get_data(as_text=True))
assert s["total"] == 31 and s["meta"]["connected"] and not s["google"]["connected"]
print("PASS /status json for live polling")

# 4. delegation invite round-trip
r = c.get("/delegate/google")
html = r.get_data(as_text=True)
assert "/invite/" in html and "read-only" in html
token = html.split("/invite/")[1].split('"')[0].split("<")[0].strip()
with db.connect() as conn:
    row = auth.resolve_invite(conn, token)
    assert row and row["provider"] == "google"
print("PASS invite link created and resolvable")

r = c.get(f"/invite/{token}", follow_redirects=False)
assert r.status_code == 302 and "/connect/google" in r.headers["Location"]
print("PASS invite redirects delegate straight into the google flow")

r = c.get(f"/invite/{token}")
assert "already been used" in r.get_data(as_text=True)
print("PASS invite is single-use with a clear message")

# 5. state mismatch is handled, not crashed
r = c.get("/connect/meta/callback?state=wrong&code=x")
assert r.status_code == 400 and "expired" in r.get_data(as_text=True)
print("PASS csrf state mismatch gives a human error")
