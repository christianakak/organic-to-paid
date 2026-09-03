"""Verify the pre-flight checks fire, and stay quiet on clean input.

Every check here is tested in both directions. A guard that has only
ever passed and a guard that is broken produce identical output, and
`doctor` exists precisely to catch a class of error that otherwise looks
like success — so it is the last thing that should be taken on trust.

No network. Only the local shape and parser checks are exercised; the
ones that need a live endpoint are covered by running `doctor` against
real credentials.
"""
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ["ANGLE_DB"] = "/tmp/doctor_test.db"

import importlib
import config; importlib.reload(config)
import doctor; importlib.reload(doctor)


def statuses(rep, name):
    return [s for s, n, _, _ in rep.lines if n == name]


# ------------------------------------------------------------------
# Search Console: the identifier that returns empty instead of erroring
# ------------------------------------------------------------------

def gsc(site_url, creds=""):
    config.GSC_SITE_URL = site_url
    config.GOOGLE_CREDENTIALS = creds
    rep = doctor.Report()

    # Stand in for the OAuth lookup so nothing touches the network.
    from sources import google
    real = google._oauth
    google._oauth = lambda conn, account: (None, site_url, None)
    try:
        doctor.check_gsc(rep, None, "test")
    finally:
        google._oauth = real
    return rep


r = gsc("leira.no")
assert doctor.FAIL in statuses(r, "search console"), \
    "a bare domain is neither a domain property nor a URL prefix"
print("PASS: bare domain rejected")

r = gsc("https://leira.no")
assert doctor.WARN in statuses(r, "search console"), \
    "URL-prefix property without a trailing slash must warn"
print("PASS: missing trailing slash warned")

# The other direction: a well-formed identifier must produce no shape
# complaint at all. Without this the checks above prove only that the
# function can say FAIL, not that it says it for the right reason.
r = gsc("sc-domain:leira.no")
assert doctor.FAIL not in [s for s, n, d, _ in r.lines
                           if n == "search console" and "malformed" in d], \
    "a valid domain property must not be reported as malformed"
r = gsc("https://leira.no/")
assert doctor.WARN not in statuses(r, "search console"), \
    "a trailing-slash URL prefix must not warn"
print("PASS: well-formed identifiers stay quiet")


# ------------------------------------------------------------------
# GA4: the measurement id pasted where the property id goes
# ------------------------------------------------------------------

def ga4(property_id):
    config.GA4_PROPERTY_ID = property_id
    config.GOOGLE_CREDENTIALS = ""
    rep = doctor.Report()
    from sources import google
    real = google._oauth
    google._oauth = lambda conn, account: (None, None, property_id)
    try:
        doctor.check_ga4(rep, None, "test")
    finally:
        google._oauth = real
    return rep


r = ga4("G-4X8QK2")
assert doctor.FAIL in statuses(r, "ga4"), "measurement id must be rejected"
print("PASS: G-XXXX measurement id rejected")

r = ga4("487229104")
assert not [d for s, n, d, _ in r.lines if n == "ga4" and "numeric" in d], \
    "a numeric property id must not be reported as non-numeric"
print("PASS: numeric property id accepted")


# ------------------------------------------------------------------
# Transcripts: the parser that returns one blob instead of turns
# ------------------------------------------------------------------
#
# This is the failure that does not announce itself. A one-turn parse
# still runs the whole pipeline; it just attributes every claim to one
# undifferentiated speaker, so the rep's pitch and the customer's
# objection become the same voice.

TURNS = """Rep: Thanks for taking the time today.
Kunde: No problem. I've been looking at these for a while.
Rep: What's held you back?
Kunde: Honestly I was worried it wouldn't fit our space.
"""

BLOB = ("So we talked for a while and the main thing that came up was "
        "whether it would fit, and also the price, and then we went "
        "through the delivery options and that was mostly it really.")

with tempfile.TemporaryDirectory() as d:
    (pathlib.Path(d) / "call.txt").write_text(TURNS, encoding="utf-8")
    os.environ["ANGLE_TRANSCRIPT_DIR"] = d
    rep = doctor.Report()
    doctor.check_transcripts(rep)
    assert doctor.OK in statuses(rep, "transcripts"), \
        f"speaker-labelled transcript should parse: {rep.render()}"
    print("PASS: speaker-labelled transcript parses into turns")

with tempfile.TemporaryDirectory() as d:
    (pathlib.Path(d) / "call.txt").write_text(BLOB, encoding="utf-8")
    os.environ["ANGLE_TRANSCRIPT_DIR"] = d
    rep = doctor.Report()
    doctor.check_transcripts(rep)
    assert doctor.FAIL in statuses(rep, "transcripts"), \
        "an unsplit transcript must be reported, not silently accepted"
    print("PASS: unsplit transcript reported")
