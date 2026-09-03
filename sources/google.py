"""Google sources — Search Console queries and GA4 behaviour.

GSC gives demand language: what people typed to arrive. Treat it as
intent vocabulary, not truth — the data is sampled and keyword-
incomplete, and anonymised queries are dropped entirely.

GA4 gives two things worth having:
  1. landing-page engagement + conversions, used to weight claims by
     conversion proximity
  2. on-site search terms (view_search_results), which are people
     typing what they could not find. Under-mined and high intent.
"""

from datetime import date, timedelta

import config

_SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
]


def _oauth(conn, account):
    """Prefer a stored OAuth connection; fall back to service account.

    OAuth is what clients go through (one button). Service accounts stay
    supported for running the tool on your own properties.
    """
    try:
        import auth
        row = auth.get_connection(conn, account, "google")
        if row:
            token = auth.google_token(conn, account)
            if token:
                return token, row["gsc_site_url"], row["ga4_property_id"]
    except Exception:
        pass
    return None, config.GSC_SITE_URL, config.GA4_PROPERTY_ID


def _service_creds():
    from google.oauth2 import service_account
    creds = service_account.Credentials.from_service_account_file(
        config.GOOGLE_CREDENTIALS, scopes=_SCOPES
    )
    # Domain-wide delegation. Impersonating a Workspace user makes the
    # service account inherit that person's own Search Console and
    # Analytics access, which deletes the step where someone adds an
    # email by hand inside two consoles and gets no feedback when they
    # get it wrong. Without a subject the credentials still work, but
    # only for properties explicitly shared with the service account's
    # own address — the old, painful route.
    if config.GOOGLE_IMPERSONATE:
        creds = creds.with_subject(config.GOOGLE_IMPERSONATE)
    return creds


def _bearer(token):
    """Wrap a raw OAuth access token as google-auth credentials."""
    from google.oauth2.credentials import Credentials
    return Credentials(token=token)


def _window():
    end = date.today() - timedelta(days=2)      # GSC lags ~2 days
    start = end - timedelta(days=min(config.LOOKBACK_DAYS, 480))
    return start.isoformat(), end.isoformat()


# ------------------------------------------------------------------
# Search Console
# ------------------------------------------------------------------

def pull_gsc(conn, account):
    token, site_url, _ = _oauth(conn, account)
    if not site_url:
        return 0
    if not token and not config.GOOGLE_CREDENTIALS:
        return 0

    from googleapiclient.discovery import build
    import db

    creds = _bearer(token) if token else _service_creds()
    service = build("searchconsole", "v1", credentials=creds,
                    cache_discovery=False)
    start, end = _window()

    n, start_row = 0, 0
    while True:
        resp = service.searchanalytics().query(
            siteUrl=site_url,
            body={
                "startDate": start,
                "endDate": end,
                "dimensions": ["query"],
                "rowLimit": 25000,
                "startRow": start_row,
                "dataState": "final",
            },
        ).execute()

        rows = resp.get("rows", [])
        if not rows:
            break

        for r in rows:
            q = r["keys"][0]
            sid = db.insert_signal(
                conn, account, "gsc", "query", q,
                external_id=f"gsc:{q}",
                impressions=int(r.get("impressions", 0)),
                clicks=int(r.get("clicks", 0)),
                raw=r,
            )
            if sid:
                n += 1

        start_row += len(rows)
        if len(rows) < 25000:
            break

    return n


# ------------------------------------------------------------------
# GA4
# ------------------------------------------------------------------

def pull_ga4(conn, account):
    token, _, property_id = _oauth(conn, account)
    if not property_id:
        return 0
    if not token and not config.GOOGLE_CREDENTIALS:
        return 0

    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        DateRange, Dimension, Metric, RunReportRequest,
    )
    import db

    creds = _bearer(token) if token else _service_creds()
    client = BetaAnalyticsDataClient(credentials=creds)
    start, end = _window()
    prop = f"properties/{property_id}"
    n = 0

    # --- landing pages: used for conversion proximity weighting ----
    req = RunReportRequest(
        property=prop,
        date_ranges=[DateRange(start_date=start, end_date=end)],
        dimensions=[Dimension(name="landingPagePlusQueryString"),
                    Dimension(name="pageTitle")],
        metrics=[Metric(name="sessions"),
                 Metric(name="userEngagementDuration"),
                 Metric(name="conversions")],
        limit=1000,
    )
    for row in client.run_report(req).rows:
        path, title = row.dimension_values[0].value, row.dimension_values[1].value
        sessions = int(row.metric_values[0].value or 0)
        conversions = int(float(row.metric_values[2].value or 0))
        sid = db.insert_signal(
            conn, account, "ga4", "page", title or path,
            external_id=f"ga4:page:{path}",
            permalink=path,
            impressions=sessions,
            conversions=conversions,
        )
        if sid:
            n += 1

    # --- on-site search terms: people typing what they can't find --
    try:
        req = RunReportRequest(
            property=prop,
            date_ranges=[DateRange(start_date=start, end_date=end)],
            dimensions=[Dimension(name="searchTerm")],
            metrics=[Metric(name="eventCount")],
            limit=2000,
        )
        for row in client.run_report(req).rows:
            term = row.dimension_values[0].value
            if not term or term == "(not set)":
                continue
            sid = db.insert_signal(
                conn, account, "ga4", "query", term,
                external_id=f"ga4:search:{term}",
                impressions=int(row.metric_values[0].value or 0),
            )
            if sid:
                n += 1
    except Exception as e:
        # Site search not configured on the property is common and not
        # fatal — but so is a permissions problem, and the two used to
        # be indistinguishable from the outside.
        db.record(conn, account, "ga4", "skipped",
                  f"site-search terms unavailable "
                  f"(usually means site search was never configured): {e}")

    return n


def pull(conn, account):
    return pull_gsc(conn, account) + pull_ga4(conn, account)
