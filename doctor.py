"""Pre-flight credential check.

This exists because of one failure mode. Google's APIs answer a wrongly
identified property with an empty result set rather than an error: the
pull runs, reports success, and stores nothing. A pull that got zero rows
because the site URL is wrong looks exactly like a pull that got zero
rows because there is no data.

So every check here either reads at least one real row or says precisely
what it could not do. Nothing is written to the signal table.

    python run.py --account bty doctor
"""

import json
import os
from pathlib import Path

import config

OK, WARN, FAIL, SKIP = "  ok  ", " warn ", " FAIL ", "  --  "


class Report:
    def __init__(self):
        self.lines = []
        self.failures = 0

    def add(self, status, name, detail, hint=""):
        if status is FAIL:
            self.failures += 1
        self.lines.append((status, name, detail, hint))

    def render(self):
        out = []
        for status, name, detail, hint in self.lines:
            out.append(f"[{status}] {name:<22} {detail}")
            if hint:
                for line in hint.split("\n"):
                    out.append(f"{'':>9}{line}")
        return "\n".join(out)


# ------------------------------------------------------------------
# LLM
# ------------------------------------------------------------------

def check_anthropic(rep):
    if not config.ANTHROPIC_API_KEY:
        rep.add(FAIL, "anthropic", "no API key",
                "Set ANTHROPIC_API_KEY. Needed by `claims` and `brief`,\n"
                "not by `pull` or `score`.")
        return
    rep.add(OK, "anthropic", f"key set, model {config.MODEL}")


# ------------------------------------------------------------------
# Meta
# ------------------------------------------------------------------

def check_meta(rep, conn, account):
    from sources import meta

    token, page_id, ig_id = meta._creds(conn, account)

    if not token:
        rep.add(SKIP, "meta", "not configured",
                "Set META_TOKEN and META_PAGE_ID, or connect via\n"
                "`python onboard.py`. This is the required source —\n"
                "comment threads are the highest-signal input.")
        return
    if not page_id:
        rep.add(FAIL, "meta", "token present, no page id",
                "Set META_PAGE_ID. A user token with no page selected\n"
                "cannot read organic insights.")
        return

    try:
        page = meta._get(page_id, {"fields": "name,fan_count"}, token)
        rep.add(OK, "meta page", f"{page.get('name', page_id)} ({page_id})")
    except Exception as e:
        rep.add(FAIL, "meta page", str(e)[:200],
                "A 190 error means the token expired. A 100 means the\n"
                "page id is wrong. A 200 means a missing scope —\n"
                "pages_read_engagement and read_insights are required.")
        return

    # One post, with its insights, is the only proof that the scopes are
    # actually sufficient. Listing posts works with far fewer permissions
    # than reading their metrics does.
    try:
        posts = meta._paged(f"{page_id}/posts",
                            {"fields": "id,message,created_time"}, token, 1)
        if not posts:
            rep.add(WARN, "meta posts", "0 posts in range",
                    f"Nothing published in the last "
                    f"{config.LOOKBACK_DAYS} days, or the page is empty.")
        else:
            rep.add(OK, "meta posts", f"reachable, newest {posts[0]['id']}")
            _probe_comments(rep, meta, posts[0]["id"], token)
    except Exception as e:
        rep.add(FAIL, "meta posts", str(e)[:200])

    if not ig_id:
        rep.add(SKIP, "instagram", "no IG user id",
                "Optional. Set META_IG_USER_ID for the linked business\n"
                "account if you want Instagram in the corpus.")
        return

    try:
        media = meta._paged(f"{ig_id}/media", {"fields": "id,caption"},
                            token, 1)
        if not media:
            rep.add(WARN, "instagram", "0 media returned")
            return
        ins = meta._get(f"{media[0]['id']}/insights",
                        {"metric": "reach,saved,shares"}, token)
        names = [r["name"] for r in ins.get("data", [])]
        missing = [m for m in ("reach", "saved", "shares") if m not in names]
        if missing:
            rep.add(WARN, "instagram insights",
                    f"missing {', '.join(missing)}",
                    "Instagram renames metrics between API versions and\n"
                    "the adapter treats a missing metric as absent data.\n"
                    f"Currently requesting {config.META_API_VERSION}.\n"
                    "Check the current metric names before trusting any\n"
                    "save or share weighting from Instagram.")
        else:
            rep.add(OK, "instagram insights", "reach, saved, shares all present")
    except Exception as e:
        rep.add(FAIL, "instagram", str(e)[:200])


def _probe_comments(rep, meta, post_id, token):
    try:
        comments = meta._paged(
            f"{post_id}/comments",
            {"fields": "id,message,created_time", "filter": "stream"},
            token, 1,
        )
        if comments:
            rep.add(OK, "meta comments", "readable")
        else:
            rep.add(WARN, "meta comments", "newest post has none",
                    "Not conclusive on its own — check a post you know\n"
                    "has comments before assuming the scope is fine.")
    except Exception as e:
        rep.add(FAIL, "meta comments", str(e)[:200],
                "This is the source the whole product leans on.")


# ------------------------------------------------------------------
# Google
# ------------------------------------------------------------------

def check_gsc(rep, conn, account):
    from sources import google

    token, site_url, _ = google._oauth(conn, account)
    if not site_url:
        rep.add(SKIP, "search console", "GSC_SITE_URL not set")
        return
    # Shape check first — it is free, needs no credentials, and both of
    # these mistakes return empty rather than erroring once they reach
    # the API, which is the confusion this whole command exists to end.
    if not site_url.startswith(("sc-domain:", "http://", "https://")):
        rep.add(FAIL, "search console", f"malformed: {site_url}",
                "A domain property is 'sc-domain:example.com'.\n"
                "A URL-prefix property is the exact prefix, protocol\n"
                "and trailing slash included.")
        return
    if site_url.startswith("http") and not site_url.endswith("/"):
        rep.add(WARN, "search console", "URL-prefix without trailing slash",
                "GSC matches this string exactly. A missing trailing\n"
                "slash returns empty results rather than an error.")

    if not token and not config.GOOGLE_CREDENTIALS:
        rep.add(FAIL, "search console", "site set but no credentials",
                "Set GOOGLE_APPLICATION_CREDENTIALS, or connect via\n"
                "`python onboard.py`.")
        return

    try:
        from googleapiclient.discovery import build
        creds = google._bearer(token) if token else google._service_creds()
        service = build("searchconsole", "v1", credentials=creds,
                        cache_discovery=False)
        start, end = google._window()
        resp = service.searchanalytics().query(
            siteUrl=site_url,
            body={"startDate": start, "endDate": end,
                  "dimensions": ["query"], "rowLimit": 1,
                  "dataState": "final"},
        ).execute()
    except Exception as e:
        rep.add(FAIL, "search console", str(e)[:200],
                "A 403 means the service account is not a user on the\n"
                "property. Add it under Settings -> Users and permissions.")
        return

    rows = resp.get("rows", [])
    if rows:
        rep.add(OK, "search console", f"{site_url} -> queries readable")
    else:
        rep.add(FAIL, "search console", f"{site_url} -> 0 rows",
                "Credentials work but the property returned nothing.\n"
                "Almost always the property identifier rather than\n"
                "access. Check it matches Search Console exactly.\n"
                "GSC also lags about two days and drops anonymised\n"
                "queries entirely.")


def check_ga4(rep, conn, account):
    from sources import google

    token, _, property_id = google._oauth(conn, account)
    if not property_id:
        rep.add(SKIP, "ga4", "GA4_PROPERTY_ID not set")
        return
    # Shape before credentials, for the same reason as GSC: pasting the
    # G-XXXXXXX measurement id here is the common mistake and it needs
    # no network call to catch.
    if not str(property_id).isdigit():
        rep.add(FAIL, "ga4", f"not a numeric id: {property_id}",
                "This wants the number from GA4 Admin -> Property\n"
                "details, not the G-XXXXXXX measurement id.")
        return

    if not token and not config.GOOGLE_CREDENTIALS:
        rep.add(FAIL, "ga4", "property set but no credentials")
        return

    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import (
            DateRange, Dimension, Metric, RunReportRequest,
        )
        creds = google._bearer(token) if token else google._service_creds()
        client = BetaAnalyticsDataClient(credentials=creds)
        start, end = google._window()
        resp = client.run_report(RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[DateRange(start_date=start, end_date=end)],
            dimensions=[Dimension(name="landingPagePlusQueryString")],
            metrics=[Metric(name="sessions")],
            limit=1,
        ))
    except Exception as e:
        rep.add(FAIL, "ga4", str(e)[:200],
                "A PERMISSION_DENIED means the service account is not a\n"
                "Viewer on the property. Add it under Admin ->\n"
                "Property access management.")
        return

    if list(resp.rows):
        rep.add(OK, "ga4", f"property {property_id} -> pages readable")
    else:
        rep.add(FAIL, "ga4", f"property {property_id} -> 0 rows",
                "Credentials work but the property returned nothing.\n"
                "Usually the wrong property number.")


# ------------------------------------------------------------------
# First-party
# ------------------------------------------------------------------

def check_transcripts(rep):
    from sources import firstparty

    directory = Path(os.getenv("ANGLE_TRANSCRIPT_DIR", ""))
    if not directory or not directory.is_dir():
        rep.add(SKIP, "transcripts", f"no directory at {directory}")
        return

    files = [p for p in sorted(directory.iterdir())
             if p.suffix.lower() in (".txt", ".vtt", ".srt", ".json")]
    if not files:
        rep.add(SKIP, "transcripts", f"{directory} is empty",
                "Drop .txt, .vtt, .srt or .json exports in here.\n"
                "Rated the single highest-signal source: objections\n"
                "said out loud.")
        return

    # The real risk is not a missing file, it's a parser that returns the
    # whole transcript as one turn. That still "works" and quietly
    # destroys the speaker attribution the claim pass depends on.
    turns = list(firstparty._parse_transcript(files[0]))
    if len(turns) <= 1:
        rep.add(FAIL, "transcripts",
                f"{len(files)} files, {files[0].name} parsed as "
                f"{len(turns)} turn(s)",
                "The parser did not split this into speaker turns. It\n"
                "will still run, but every claim gets attributed to one\n"
                "blob and rep talk mixes with customer talk. Fix\n"
                "_parse_transcript for this export format first.")
        return

    speakers = {s for s, _ in turns if s}
    rep.add(OK, "transcripts",
            f"{len(files)} files, {files[0].name} -> {len(turns)} turns, "
            f"{len(speakers)} speakers")


def check_email_csv(rep):
    path = os.getenv("ANGLE_EMAIL_CSV", "")
    if not path:
        rep.add(SKIP, "email csv", "ANGLE_EMAIL_CSV not set")
        return
    if not Path(path).is_file():
        rep.add(FAIL, "email csv", f"no file at {path}")
        return
    rep.add(OK, "email csv", path)


def check_trustpilot(rep):
    if not os.getenv("TRUSTPILOT_API_KEY", ""):
        rep.add(SKIP, "trustpilot", "TRUSTPILOT_API_KEY not set")
        return
    if not os.getenv("TRUSTPILOT_BUSINESS_UNIT_ID", ""):
        rep.add(FAIL, "trustpilot", "key set, no business unit id",
                "Set TRUSTPILOT_BUSINESS_UNIT_ID.")
        return
    rep.add(OK, "trustpilot", "key and business unit set")


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def run(conn, account):
    rep = Report()
    check_anthropic(rep)
    check_meta(rep, conn, account)
    check_gsc(rep, conn, account)
    check_ga4(rep, conn, account)
    check_transcripts(rep)
    check_email_csv(rep)
    check_trustpilot(rep)
    return rep
