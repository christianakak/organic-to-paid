"""HappyScribe — call transcripts, synced from a folder.

Rated the highest-signal source in the whole design: objections said out
loud, with the rep's answer sitting next to them. The old route was
"export files and copy them into a directory", which is the kind of step
that quietly never happens.

Auth is a bearer API key from account settings — HappyScribe has no user
OAuth. One paste, then pick a folder.

Each speaker turn becomes its own signal, so the claim pass can tell the
customer's objection from the rep's pitch. That split is why
`_parse_transcript` exists in `firstparty.py`, and it is reused here
rather than reimplemented; when a transcript comes back as one
undifferentiated blob the whole attribution collapses, which is exactly
what `doctor` checks for.
"""

import requests

import db
from pipeline import scrub

BASE = "https://www.happyscribe.com/api/v1"


def _key(conn, account):
    import auth
    return auth.get_settings(conn, account, "happyscribe").get("api_key")


def _get(path, key, params=None):
    r = requests.get(
        f"{BASE}/{path}",
        headers={"Authorization": f"Bearer {key}"},
        params=params or {},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"HappyScribe {r.status_code}: {r.text[:300]}")
    return r.json()


def verify(key):
    """One call, to prove the key works before it is stored."""
    _get("transcriptions", key, {"limit": 1})
    return True


def list_folders(conn, account):
    """For the picker."""
    key = _key(conn, account)
    if not key:
        return []
    data = _get("folders", key)
    items = data if isinstance(data, list) else data.get("results", [])
    return [{"id": f.get("id"), "name": f.get("name", f.get("id"))}
            for f in items]


def _transcript_text(tid, key):
    """Fetch the transcript as plain text with speaker labels.

    The txt export keeps `Speaker 1:` prefixes, which is what
    `_parse_transcript` splits on. A JSON export would need its own
    parser for no gain.
    """
    r = requests.get(
        f"{BASE}/exports",
        headers={"Authorization": f"Bearer {key}"},
        params={"transcription_id": tid, "format": "txt"},
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"HappyScribe export {r.status_code}: "
                           f"{r.text[:200]}")
    return r.text


def pull(conn, account, folder_id=None, limit=200):
    import auth
    from sources.firstparty import _parse_transcript_text

    settings = auth.get_settings(conn, account, "happyscribe")
    key = settings.get("api_key")
    folder_id = folder_id or settings.get("folder_id")

    if not key:
        db.record(conn, account, "happyscribe", "skipped", "no API key")
        return 0

    params = {"limit": limit}
    if folder_id:
        params["folder_id"] = folder_id

    try:
        data = _get("transcriptions", key, params)
    except RuntimeError as e:
        db.record(conn, account, "happyscribe", "error", str(e))
        return 0

    items = data if isinstance(data, list) else data.get("results", [])
    n, blobs = 0, 0

    for t in items:
        tid = t.get("id")
        if not tid or t.get("state") not in (None, "automatic_done",
                                             "human_done", "done"):
            continue
        try:
            text = _transcript_text(tid, key)
        except RuntimeError as e:
            db.record(conn, account, "happyscribe", "dropped",
                      f"transcript {tid}: {e}")
            continue

        turns = list(_parse_transcript_text(text))
        if len(turns) <= 1:
            # Still ingest it, but say so. A one-turn parse merges the
            # rep's pitch with the customer's objection into one voice,
            # and nothing downstream would notice.
            blobs += 1

        for i, (speaker, turn) in enumerate(turns):
            cleaned = scrub.scrub(turn)
            if len(cleaned) < 12:
                continue
            sid = db.insert_signal(
                conn, account, "transcript", "comment", cleaned,
                external_id=f"hs:{tid}:{i}",
                parent_id=str(tid),
                created_at=t.get("createdAt") or t.get("created_at"),
                raw={"speaker": speaker, "transcription_id": tid},
            )
            if sid:
                n += 1

    if blobs:
        db.record(conn, account, "happyscribe", "dropped",
                  f"{blobs} transcript(s) did not split into speaker turns "
                  f"— claims from these cannot be attributed to customer "
                  f"vs rep")
    db.record(conn, account, "happyscribe", "ok", f"{n} speaker turns")
    return n
