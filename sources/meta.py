"""Meta Graph API — organic Page and Instagram signal.

Pulls posts, their organic insights, and (importantly) comment threads.
Comments are the highest-signal text in the Meta corpus and the part
most tooling ignores.

Known API constraints handled here:
  - boosted posts blend organic + paid metrics -> flagged is_boosted,
    text retained for mining, engagement excluded from scoring
  - IG insight endpoints churn between versions; missing metrics are
    tolerated rather than fatal
  - demographic breakdowns need 100+ people, so we don't request them
  - reshared video insights return 0; treated as NULL not 0
"""

import time
from datetime import datetime, timedelta, timezone

import requests

import config
import db
from pipeline import scrub

BASE = "https://graph.facebook.com"


def _creds(conn, account):
    """Stored OAuth connection wins; env is the fallback for self-runs."""
    try:
        import auth
        row = auth.get_connection(conn, account, "meta")
        if row and row["access_token"] and row["meta_page_id"]:
            return (row["access_token"], row["meta_page_id"],
                    row["meta_ig_user_id"])
    except Exception as e:
        # Falling back to env is correct for self-runs, but doing it
        # because the connection lookup itself broke is not the same
        # thing and should not look the same.
        try:
            db.record(conn, account, "meta", "skipped",
                      f"stored connection unreadable, using env: {e}")
        except Exception:
            pass
    return config.META_TOKEN, config.META_PAGE_ID, config.META_IG_USER_ID


def _get(path, params, token, retries=3):
    url = f"{BASE}/{config.META_API_VERSION}/{path}"
    params = {**params, "access_token": token}
    for attempt in range(retries):
        r = requests.get(url, params=params, timeout=30)
        if r.status_code == 200:
            return r.json()
        # rate limit / transient
        if r.status_code in (429, 500, 502, 503):
            time.sleep(2 ** attempt)
            continue
        # surface the actual Meta error, they're usually informative
        raise RuntimeError(f"Meta API {r.status_code}: {r.text[:400]}")
    raise RuntimeError(f"Meta API failed after {retries} attempts: {path}")


def _paged(path, params, token, limit):
    """Follow cursor pagination up to `limit` items."""
    out = []
    params = {**params, "limit": min(100, limit)}
    while len(out) < limit:
        data = _get(path, params, token)
        out.extend(data.get("data", []))
        nxt = data.get("paging", {}).get("cursors", {}).get("after")
        if not nxt or not data.get("data"):
            break
        params["after"] = nxt
    return out[:limit]


def _zero_to_none(v):
    """Reshared video insights come back as 0 rather than absent."""
    return None if v in (0, None) else v


# ------------------------------------------------------------------
# Facebook Page
# ------------------------------------------------------------------

def pull_page(conn, account, token=None, page_id=None):
    if token is None:
        token, page_id, _ = _creds(conn, account)
    if not (token and page_id):
        return 0

    since = int(
        (datetime.now(timezone.utc)
         - timedelta(days=config.LOOKBACK_DAYS)).timestamp()
    )

    posts = _paged(
        f"{page_id}/posts",
        {
            "fields": (
                "id,message,created_time,permalink_url,"
                "is_eligible_for_promotion,"
                "shares,"
                "insights.metric("
                "post_impressions_organic_unique,"
                "post_impressions_unique,"
                "post_reactions_by_type_total,"
                "post_clicks)"
            ),
            "since": since,
        },
        token,
        config.MAX_POSTS,
    )

    n = 0
    for p in posts:
        ins = {}
        for row in p.get("insights", {}).get("data", []):
            vals = row.get("values") or [{}]
            ins[row["name"]] = vals[0].get("value")

        reactions = ins.get("post_reactions_by_type_total")
        if isinstance(reactions, dict):
            reactions = sum(reactions.values())

        # is_eligible_for_promotion=False on an active post is the
        # usual tell for "already boosted".
        boosted = p.get("is_eligible_for_promotion") is False

        sid = db.insert_signal(
            conn, account, "meta_page", "post",
            p.get("message", ""),
            external_id=p["id"],
            permalink=p.get("permalink_url"),
            created_at=p.get("created_time"),
            reach=_zero_to_none(ins.get("post_impressions_organic_unique")),
            impressions=_zero_to_none(ins.get("post_impressions_unique")),
            shares=(p.get("shares") or {}).get("count"),
            reactions=reactions,
            clicks=_zero_to_none(ins.get("post_clicks")),
            is_boosted=boosted,
            raw=p,
        )
        if sid:
            n += 1
        n += _pull_comments(conn, account, "meta_page", p["id"], token)

    return n


# ------------------------------------------------------------------
# Instagram
# ------------------------------------------------------------------

def pull_instagram(conn, account, token=None, ig_id=None):
    if token is None:
        token, _, ig_id = _creds(conn, account)
    if not (token and ig_id):
        return 0

    media = _paged(
        f"{ig_id}/media",
        {
            "fields": (
                "id,caption,timestamp,permalink,media_type,"
                "like_count,comments_count"
            )
        },
        token,
        config.MAX_POSTS,
    )

    n = 0
    for m in media:
        ins = _ig_insights(m["id"], token, conn, account)
        sid = db.insert_signal(
            conn, account, "meta_ig", "post",
            m.get("caption", ""),
            external_id=m["id"],
            permalink=m.get("permalink"),
            created_at=m.get("timestamp"),
            reach=_zero_to_none(ins.get("reach")),
            saves=_zero_to_none(ins.get("saved")),
            shares=_zero_to_none(ins.get("shares")),
            reactions=m.get("like_count"),
            comment_count=m.get("comments_count"),
            raw=m,
        )
        if sid:
            n += 1
        n += _pull_comments(conn, account, "meta_ig", m["id"], token)

    return n


def _ig_insights(media_id, token, conn=None, account=None):
    """IG metric names move between API versions. Degrade, but say so.

    This is the likeliest thing in the whole adapter to break, because
    Instagram renames metrics between versions and the failure looks
    exactly like a post with no engagement. Every drop is recorded, and
    so is every metric that was asked for and did not come back.
    """
    metrics = "reach,saved,shares"
    try:
        data = _get(f"{media_id}/insights", {"metric": metrics}, token)
    except RuntimeError as e:
        if conn is not None:
            db.record(conn, account, "meta_ig", "dropped",
                      f"insights unavailable for media {media_id}: {e}")
        return {}

    out = {}
    for row in data.get("data", []):
        vals = row.get("values") or [{}]
        out[row["name"]] = vals[0].get("value")

    missing = [m for m in metrics.split(",") if m not in out]
    if missing and conn is not None:
        db.record(conn, account, "meta_ig", "dropped",
                  f"media {media_id}: API returned no "
                  f"{', '.join(missing)} — metric may have been renamed")
    return out


# ------------------------------------------------------------------
# Comments — the payload that matters
# ------------------------------------------------------------------

def _pull_comments(conn, account, source, post_id, token):
    try:
        comments = _paged(
            f"{post_id}/comments",
            {"fields": "id,message,text,created_time,timestamp,like_count",
             "filter": "stream"},
            token,
            config.MAX_COMMENTS_PER_POST,
        )
    except RuntimeError as e:
        # Comments are the highest-signal text in the corpus. Losing a
        # thread quietly is the worst failure this adapter can have.
        db.record(conn, account, source, "dropped",
                  f"comments unavailable for post {post_id}: {e}")
        return 0

    n = 0
    for c in comments:
        # Commenters are members of the public who did not sign up to be
        # in an ad research corpus. Scrubbed on the way in, and the raw
        # payload (which carries the commenter's name and profile id) is
        # not kept — storing it would undo the scrubbing.
        text = scrub.scrub(c.get("message") or c.get("text") or "")
        if not text:
            continue
        sid = db.insert_signal(
            conn, account, source, "comment", text,
            external_id=c["id"],
            parent_id=post_id,
            created_at=c.get("created_time") or c.get("timestamp"),
            reactions=c.get("like_count"),
            raw={"post_id": post_id},
        )
        if sid:
            n += 1
    return n


def pull(conn, account):
    token, page_id, ig_id = _creds(conn, account)
    if not token:
        return 0
    return (pull_page(conn, account, token, page_id)
            + pull_instagram(conn, account, token, ig_id))
