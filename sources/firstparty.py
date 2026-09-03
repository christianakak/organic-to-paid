"""First-party sources — the highest-signal corpora, and the ones
nobody mines.

transcripts : sales / screening calls. Objections stated out loud,
              pre-rationalisation, in the customer's own words. This is
              the best cold-ad angle source that exists and it's fully
              first-party, so no scraping and no third-party GDPR issue.

email       : subject lines + open rates. Every subject line a business
              ever sent is a hook test that already ran, with a clean
              metric. Two years of campaigns = hundreds of pre-run
              experiments nobody has looked at as a dataset.

reviews     : Trustpilot (sanctioned API, dominant in the Nordics) and
              Google Business Profile. Objection-dense and legal, unlike
              scraping review sites that forbid it.
"""

import csv
import json
import os
from pathlib import Path

import requests

import config
import db
from pipeline import scrub


# ------------------------------------------------------------------
# Call transcripts
# ------------------------------------------------------------------

def pull_transcripts(conn, account, directory=None):
    """Ingest transcripts from a local directory.

    Accepts .txt, .vtt, .srt, or .json (HappyScribe export format).
    Each speaker turn becomes its own signal so that claim extraction
    can attribute objections to the customer rather than the rep.
    """
    directory = directory or os.getenv("ANGLE_TRANSCRIPT_DIR", "")
    if not directory or not Path(directory).is_dir():
        return 0

    n = 0
    for path in sorted(Path(directory).iterdir()):
        if path.suffix.lower() not in (".txt", ".vtt", ".srt", ".json"):
            continue
        for i, (speaker, raw_text) in enumerate(_parse_transcript(path)):
            text = scrub.scrub(raw_text)
            if not text:
                continue
            # Rep turns are pitch, not signal. Customer turns are gold.
            # Heuristic: keep everything, tag speaker in external_id so
            # the claim pass can weigh it.
            sid = db.insert_signal(
                conn, account, "transcript", "comment", text,
                external_id=f"tx:{path.name}:{i}",
                parent_id=path.name,
                raw={"speaker": speaker, "file": path.name},
            )
            if sid:
                n += 1
    return n


def _parse_transcript(path):
    """Yield (speaker, text) turns from a file on disk."""
    return _parse_transcript_text(
        path.read_text(errors="ignore"), path.suffix.lower()
    )


def _parse_transcript_text(raw, suffix=".txt"):
    """Yield (speaker, text) turns. Tolerant of messy formats.

    Split out from the file version so the HappyScribe adapter can parse
    an export it fetched over HTTP without writing it to disk first. The
    speaker split is the load-bearing part: without it the rep's pitch
    and the customer's objection collapse into one voice and every claim
    gets attributed to nobody in particular.
    """
    if suffix == ".json":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        # HappyScribe-ish shapes
        segments = (
            data.get("segments")
            or data.get("results")
            or (data if isinstance(data, list) else [])
        )
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            text = seg.get("text") or seg.get("transcript") or ""
            speaker = seg.get("speaker") or seg.get("speaker_name") or "?"
            if text.strip():
                yield str(speaker), text.strip()
        return

    # vtt/srt/txt: strip timecodes and cue numbers, group by speaker label
    buf, speaker = [], "?"
    for line in raw.splitlines():
        s = line.strip()
        if not s or s == "WEBVTT" or s.isdigit() or "-->" in s:
            continue
        if ":" in s[:40]:
            head, _, tail = s.partition(":")
            if len(head.split()) <= 4:            # looks like a name label
                if buf:
                    yield speaker, " ".join(buf)
                    buf = []
                speaker = head.strip()
                s = tail.strip()
        if s:
            buf.append(s)
    if buf:
        yield speaker, " ".join(buf)


# ------------------------------------------------------------------
# Email — subject lines as pre-run hook tests
# ------------------------------------------------------------------

def pull_email_csv(conn, account, path=None):
    """Ingest an email-campaign export.

    Expects columns (case-insensitive, extras ignored):
        subject, open_rate | opens, sent | recipients, click_rate | clicks
    Klaviyo and Mailchimp both export something close enough.
    """
    path = path or os.getenv("ANGLE_EMAIL_CSV", "")
    if not path or not Path(path).is_file():
        return 0

    n = 0
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            low = {k.lower().strip(): v for k, v in row.items() if k}
            subject = low.get("subject") or low.get("subject line") or ""
            if not subject.strip():
                continue
            opens = _num(low.get("opens") or low.get("unique opens"))
            sent = _num(low.get("sent") or low.get("recipients")
                        or low.get("delivered"))
            clicks = _num(low.get("clicks") or low.get("unique clicks"))
            rate = _num(low.get("open_rate") or low.get("open rate"))
            if opens is None and rate is not None and sent:
                opens = int(sent * (rate / 100 if rate > 1 else rate))

            sid = db.insert_signal(
                conn, account, "email", "post", subject,
                external_id=f"email:{subject[:120]}",
                impressions=sent,
                reactions=opens,       # opens ≈ hook worked
                clicks=clicks,
                raw=row,
            )
            if sid:
                n += 1
    return n


def _num(v):
    if v in (None, ""):
        return None
    try:
        return int(float(str(v).replace("%", "").replace(",", ".").strip()))
    except ValueError:
        return None


# ------------------------------------------------------------------
# Reviews — Trustpilot
# ------------------------------------------------------------------

def resolve_business_unit(api_key, domain):
    """Domain -> Trustpilot business unit id.

    The id is a 24-character hex string nobody has memorised and which
    their UI does not show you. Asking for a domain and looking it up is
    the difference between a one-line paste and a support ticket.
    """
    domain = (domain or "").strip().lower()
    for prefix in ("https://", "http://", "www."):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    domain = domain.split("/")[0]
    if not domain:
        return None, "no domain given"

    r = requests.get(
        "https://api.trustpilot.com/v1/business-units/find",
        params={"apikey": api_key, "name": domain},
        timeout=30,
    )
    if r.status_code == 401:
        return None, "API key rejected"
    if r.status_code == 404:
        return None, f"no Trustpilot profile found for {domain}"
    if r.status_code != 200:
        return None, f"Trustpilot {r.status_code}: {r.text[:200]}"

    data = r.json()
    unit_id = data.get("id")
    if not unit_id:
        return None, f"no business unit in the response for {domain}"
    return unit_id, data.get("displayName") or domain


def pull_trustpilot(conn, account):
    """Trustpilot public Business Units API.

    Needs TRUSTPILOT_API_KEY and TRUSTPILOT_BUSINESS_UNIT_ID. Using the
    sanctioned API rather than scraping matters here: review sites
    generally forbid scraping in their terms, and reviews can contain
    personal data under GDPR.
    """
    import auth

    # Stored credentials win; env is the fallback for self-runs that
    # never went through the connect flow.
    settings = auth.get_settings(conn, account, "trustpilot")
    key = settings.get("api_key") or os.getenv("TRUSTPILOT_API_KEY", "")
    unit = (settings.get("business_unit_id")
            or os.getenv("TRUSTPILOT_BUSINESS_UNIT_ID", ""))
    if not (key and unit):
        db.record(conn, account, "trustpilot", "skipped",
                  "no API key or no business unit resolved")
        return 0

    n, page = 0, 1
    while page <= 20:
        r = requests.get(
            f"https://api.trustpilot.com/v1/business-units/{unit}/reviews",
            params={"apikey": key, "perPage": 100, "page": page},
            timeout=30,
        )
        if r.status_code != 200:
            break
        reviews = r.json().get("reviews", [])
        if not reviews:
            break
        for rev in reviews:
            text = scrub.scrub(" ".join(
                filter(None, [rev.get("title"), rev.get("text")])
            ))
            if not text:
                continue
            sid = db.insert_signal(
                conn, account, "trustpilot", "comment", text,
                external_id=f"tp:{rev.get('id')}",
                created_at=rev.get("createdAt"),
                reactions=rev.get("stars"),
                # The full review payload carries the reviewer's display
                # name and country. Keeping only the stars means the
                # scrubbing above is not undone by the raw column.
                raw={"stars": rev.get("stars")},
            )
            if sid:
                n += 1
        page += 1
    return n


def pull(conn, account):
    return (
        pull_transcripts(conn, account)
        + pull_email_csv(conn, account)
        + pull_trustpilot(conn, account)
    )
