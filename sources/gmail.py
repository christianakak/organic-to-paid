"""Gmail — customers writing to you directly, in their own words.

The richest first-party text there is, and the largest privacy surface in
the project. Both facts shape every decision here.

**Bounded to one label.** Never the whole mailbox. You tag customer
threads with a label and this reads only that. The selection is
deliberate and explicable, which is what makes it defensible; a
whole-mailbox sweep is neither.

**Scrubbed before storage.** `pipeline.scrub` strips names, addresses,
phone numbers, signature blocks and quoted reply chains on the way in.
The raw body is never written to the database, so there is nothing to
leak later and nothing to go back and delete.

**Quoted chains are removed for a second reason.** A five-message thread
quotes itself, so the same sentence would arrive five times, and
recurrence across independent signals is the largest single input to the
ranking. See the note in `pipeline/scrub.py`.

Auth is a service account impersonating a Workspace user, which needs
domain-wide delegation authorised once in Admin console. The alternative
— OAuth — hands out refresh tokens that expire after seven days while the
app is in Testing, and Gmail's scope is restricted enough that leaving
Testing needs a third-party security audit.
"""

import base64
import re

import config
import db
from pipeline import scrub

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

# Gmail's own noise, before the scrubber gets a look in. These are
# machine-generated and carry no customer belief, so they are dropped
# whole rather than mined.
SKIP_FROM = re.compile(
    r"(no-?reply|donotreply|mailer-daemon|postmaster|notification|"
    r"bounce|newsletter|nyhetsbrev)",
    re.I,
)


def _creds(conn, account, subject=None):
    """Impersonated service-account credentials, or None.

    `subject` is the Workspace user being impersonated. The service
    account has no mailbox of its own — without a subject this returns
    credentials that will fail on every call, so it is required.
    """
    from google.oauth2 import service_account
    import auth

    settings = auth.get_settings(conn, account, "gmail")
    subject = subject or settings.get("impersonate") or config.GMAIL_USER
    if not (config.GOOGLE_CREDENTIALS and subject):
        return None, None

    creds = service_account.Credentials.from_service_account_file(
        config.GOOGLE_CREDENTIALS, scopes=[SCOPE],
    ).with_subject(subject)
    return creds, subject


def _service(conn, account):
    from googleapiclient.discovery import build

    creds, subject = _creds(conn, account)
    if not creds:
        return None, None
    return build("gmail", "v1", credentials=creds,
                 cache_discovery=False), subject


def list_labels(conn, account):
    """For the picker. Returns [{id, name, total}] excluding system labels."""
    service, _ = _service(conn, account)
    if not service:
        return []
    resp = service.users().labels().list(userId="me").execute()
    out = []
    for lb in resp.get("labels", []):
        # System labels are INBOX, SENT, SPAM and friends. A customer
        # label is one a person made, and that is the whole point of
        # asking for a label rather than a query.
        if lb.get("type") != "user":
            continue
        out.append({"id": lb["id"], "name": lb["name"]})
    return sorted(out, key=lambda x: x["name"].lower())


def _decode(part):
    data = (part.get("body") or {}).get("data")
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data).decode("utf-8", "replace")
    except (ValueError, TypeError):
        return ""


def _plain_text(payload):
    """Prefer text/plain. Fall back to stripping tags from text/html.

    Walking the MIME tree rather than taking payload.body directly,
    because a multipart/alternative message keeps the real text one or
    two levels down and the top-level body is empty.
    """
    if not payload:
        return ""

    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        return _decode(payload)

    for part in payload.get("parts") or []:
        text = _plain_text(part)
        if text.strip():
            return text

    if mime == "text/html":
        html = _decode(payload)
        html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html,
                      flags=re.S | re.I)
        html = re.sub(r"<br\s*/?>|</p>", "\n", html, flags=re.I)
        return re.sub(r"<[^>]+>", " ", html)

    return ""


def _header(msg, name):
    for h in (msg.get("payload", {}).get("headers") or []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def pull(conn, account, label_id=None, limit=None):
    import auth

    settings = auth.get_settings(conn, account, "gmail")
    label_id = label_id or settings.get("label_id")
    if not label_id:
        db.record(conn, account, "gmail", "skipped",
                  "no label chosen — Gmail is deliberately never pulled "
                  "unbounded")
        return 0

    service, subject = _service(conn, account)
    if not service:
        db.record(conn, account, "gmail", "skipped",
                  "no service-account credentials or no user to impersonate")
        return 0

    limit = limit or config.MAX_POSTS
    n, dropped, page_token = 0, 0, None

    while n < limit:
        resp = service.users().messages().list(
            userId="me", labelIds=[label_id],
            maxResults=min(100, limit - n), pageToken=page_token,
        ).execute()

        ids = [m["id"] for m in resp.get("messages", [])]
        if not ids:
            break

        for mid in ids:
            msg = service.users().messages().get(
                userId="me", id=mid, format="full",
            ).execute()

            sender = _header(msg, "From")
            if SKIP_FROM.search(sender):
                dropped += 1
                continue

            body = scrub.scrub(_plain_text(msg.get("payload")))
            if len(body) < 20:
                # Nothing left after scrubbing means the message was a
                # signature, a quote, or an acknowledgement. Not signal.
                dropped += 1
                continue

            # The subject is scrubbed too — "Re: Marius Andersen -
            # bestilling" is a real shape.
            subject_line = scrub.scrub(_header(msg, "Subject"))
            text = f"{subject_line}\n\n{body}" if subject_line else body

            sid = db.insert_signal(
                conn, account, "gmail", "comment", text,
                external_id=f"gmail:{mid}",
                parent_id=msg.get("threadId"),
                created_at=_header(msg, "Date"),
                # No raw payload. Storing it would undo the scrubbing,
                # which is the entire point of scrubbing at ingest.
                raw={"thread": msg.get("threadId"), "label": label_id},
            )
            if sid:
                n += 1

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    if dropped:
        db.record(conn, account, "gmail", "dropped",
                  f"{dropped} message(s) skipped: automated senders, or "
                  f"nothing left after scrubbing")
    db.record(conn, account, "gmail", "ok",
              f"{n} messages from label {label_id} as {subject}")
    return n
