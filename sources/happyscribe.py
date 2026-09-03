"""HappyScribe — call transcripts, synced from a folder.

Rated the highest-signal source in the whole design: objections said out
loud, with the rep's answer sitting next to them. The old route was
"export files and copy them into a directory", which is the kind of step
that quietly never happens.

Auth is a bearer API key from account settings — HappyScribe has no user
OAuth. One paste, then pick a folder. The organisation id is discovered
rather than asked for.

API shapes verified against https://dev.happyscribe.com on 2026-09-03.
Still never called against a live account; the failure modes below are
the ones the documentation describes, not ones that have been observed.

Three things about this API that the obvious implementation gets wrong:

**Listing transcriptions requires `organization_id`.** Omitting it is an
error, not a default. `_org_id` fetches and caches it.

**`per_page` defaults to 5.** Not 50, not 100. A pull that forgets to set
it looks like an account with five transcripts in it.

**Exports are asynchronous and two-step.** POST creates one, then you
poll GET until `state` is `ready`, then download `download_link`. A
single GET returns nothing useful.

And the one that matters most: **`show_speakers` must be true on the
export.** Without it the text comes back with no speaker labels,
`_parse_transcript_text` sees one undifferentiated blob, and every claim
in the file gets attributed to nobody — merging the rep's pitch with the
customer's objection into a single voice. The pipeline still runs. It
just quietly stops being able to tell who said what, which is the entire
reason transcripts are the best source here.
"""

import time

import requests

import db
from pipeline import scrub

BASE = "https://www.happyscribe.com/api/v1"

EXPORT_TIMEOUT = 120       # seconds to wait for one export to render
EXPORT_POLL = 2.0


def _key(conn, account):
    import auth
    return auth.get_settings(conn, account, "happyscribe").get("api_key")


def _request(method, path, key, **kw):
    r = requests.request(
        method, f"{BASE}/{path}",
        headers={"Authorization": f"Bearer {key}"},
        timeout=60, **kw,
    )
    if r.status_code not in (200, 201, 202):
        raise RuntimeError(f"HappyScribe {r.status_code} on {path}: "
                           f"{r.text[:300]}")
    return r


def _get(path, key, params=None):
    return _request("GET", path, key, params=params or {}).json()


def verify(key):
    """One call, to prove the key works before it is stored.

    `/organizations` rather than `/transcriptions`, because listing
    transcriptions needs an organization_id we do not have yet — so a
    perfectly good key would fail that check for the wrong reason.
    """
    _get("organizations", key)
    return True


def _org_id(conn, account, key):
    import auth

    settings = auth.get_settings(conn, account, "happyscribe")
    if settings.get("organization_id"):
        return settings["organization_id"]

    data = _get("organizations", key)
    items = data if isinstance(data, list) else data.get("results", [])
    if not items:
        raise RuntimeError("this API key belongs to no organisation")

    org_id = items[0].get("id")
    auth.update_settings(conn, account, "happyscribe", organization_id=org_id)
    return org_id


def list_folders(conn, account):
    """For the picker. Returns [] when folders can't be listed.

    There is no documented folders endpoint. This tries the obvious one
    and degrades to "no folder filter" rather than failing the connect
    flow — the picker template already says so plainly, and reading
    everything is a worse default than reading one folder but a much
    better one than not connecting at all.
    """
    key = _key(conn, account)
    if not key:
        return []
    try:
        data = _get("folders", key)
    except (RuntimeError, requests.RequestException) as e:
        db.record(conn, account, "happyscribe", "skipped",
                  f"no folder list available, will read everything: {e}")
        return []
    items = data if isinstance(data, list) else data.get("results", [])
    return [{"id": f.get("id"), "name": f.get("name", f.get("id"))}
            for f in items if f.get("id")]


def _transcript_text(tid, key):
    """Create an export, wait for it, download it.

    One export per transcript rather than batching ids into a single
    export: the batched download is one file, and splitting it back apart
    reliably enough to keep per-transcript attribution is not worth the
    round-trips it saves.
    """
    created = _request(
        "POST", "exports", key,
        json={
            "format": "txt",
            "transcription_ids": [tid],
            # Non-negotiable. Without speaker labels the transcript
            # parses as one blob and customer and rep become one voice.
            "show_speakers": True,
            "show_timestamps": False,
        },
    ).json()

    export_id = created.get("id")
    if not export_id:
        raise RuntimeError(f"export for {tid} came back with no id")

    deadline = time.time() + EXPORT_TIMEOUT
    while time.time() < deadline:
        export = _get(f"exports/{export_id}", key)
        state = export.get("state")
        if state == "ready":
            link = export.get("download_link")
            if not link:
                raise RuntimeError(f"export {export_id} ready with no link")
            r = requests.get(link, timeout=120)
            r.raise_for_status()
            return r.text
        if state in ("failed", "expired"):
            raise RuntimeError(f"export {export_id} {state}")
        time.sleep(EXPORT_POLL)

    raise RuntimeError(f"export {export_id} not ready after "
                       f"{EXPORT_TIMEOUT}s")


def _list_transcriptions(key, org_id, folder_id, limit):
    """Paginate. `per_page` defaults to 5, so it is always set here."""
    out, page = [], 0
    while len(out) < limit:
        params = {
            "organization_id": org_id,
            "page": page,
            "per_page": min(100, limit - len(out)),
        }
        if folder_id:
            params["folder_id"] = folder_id

        data = _get("transcriptions", key, params)
        items = data if isinstance(data, list) else data.get("results", [])
        if not items:
            break
        out.extend(items)
        page += 1
    return out[:limit]


def pull(conn, account, folder_id=None, limit=200):
    import auth
    from sources.firstparty import _parse_transcript_text

    settings = auth.get_settings(conn, account, "happyscribe")
    key = settings.get("api_key")
    folder_id = folder_id or settings.get("folder_id")

    if not key:
        db.record(conn, account, "happyscribe", "skipped", "no API key")
        return 0

    try:
        org_id = _org_id(conn, account, key)
        items = _list_transcriptions(key, org_id, folder_id, limit)
    except (RuntimeError, requests.RequestException) as e:
        db.record(conn, account, "happyscribe", "error", str(e))
        return 0

    n, blobs, failed = 0, 0, 0

    for t in items:
        tid = t.get("id")
        state = t.get("state")
        if not tid:
            continue
        # Only finished transcriptions have text to export. The state
        # vocabulary is not fully documented, so this allows anything
        # that looks done rather than enumerating.
        if state and "done" not in str(state) and state != "ready":
            continue

        try:
            text = _transcript_text(tid, key)
        except (RuntimeError, requests.RequestException) as e:
            failed += 1
            db.record(conn, account, "happyscribe", "dropped",
                      f"transcript {tid}: {e}")
            continue

        turns = list(_parse_transcript_text(text))
        if len(turns) <= 1:
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
                  f"{blobs} transcript(s) came back without speaker labels "
                  f"— claims from these cannot be attributed to customer "
                  f"vs rep. Check show_speakers on the export.")
    if failed:
        db.record(conn, account, "happyscribe", "dropped",
                  f"{failed} transcript(s) failed to export")
    db.record(conn, account, "happyscribe", "ok",
              f"{n} speaker turns from {len(items)} transcript(s)")
    return n
