"""OAuth connect flows and per-account credential storage.

Design note: this replaces service-account auth for client onboarding.

A Google service account requires the client to open Search Console,
add an email as a user, then open GA4 and do it again. Two consoles,
one copy-pasted string, no feedback if they get it wrong — the calls
just return empty. That is the highest-abandon step in the whole flow.

OAuth is one button and a picker. Service accounts remain fine for
running the tool on your own properties; see config.py.
"""

import os
import secrets
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests

import config

META_SCOPES = [
    "pages_show_list",
    "pages_read_engagement",
    "read_insights",
    "instagram_basic",
    "instagram_manage_insights",
    "business_management",
]

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
]

META_APP_ID = os.getenv("META_APP_ID", "")
META_APP_SECRET = os.getenv("META_APP_SECRET", "")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
BASE_URL = os.getenv("ANGLE_BASE_URL", "http://localhost:5000")


def _now():
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------
# Storage
# ------------------------------------------------------------------

def save_connection(conn, account, provider, **kw):
    existing = get_connection(conn, account, provider)
    fields = {
        "access_token", "refresh_token", "expires_at", "scopes",
        "meta_page_id", "meta_ig_user_id", "gsc_site_url",
        "ga4_property_id", "last_sync_at", "last_error",
    }
    data = {k: v for k, v in kw.items() if k in fields}

    if existing:
        if not data:
            return
        sets = ", ".join(f"{k} = ?" for k in data)
        conn.execute(
            f"UPDATE connection SET {sets} WHERE account = ? AND provider = ?",
            [*data.values(), account, provider],
        )
    else:
        data["account"] = account
        data["provider"] = provider
        data["connected_at"] = _now().isoformat()
        cols = ", ".join(data)
        conn.execute(
            f"INSERT INTO connection ({cols}) "
            f"VALUES ({', '.join('?' for _ in data)})",
            list(data.values()),
        )
    conn.commit()


def get_connection(conn, account, provider):
    return conn.execute(
        "SELECT * FROM connection WHERE account = ? AND provider = ?",
        (account, provider),
    ).fetchone()


# ------------------------------------------------------------------
# Meta
# ------------------------------------------------------------------

def meta_auth_url(state):
    params = {
        "client_id": META_APP_ID,
        "redirect_uri": f"{BASE_URL}/connect/meta/callback",
        "state": state,
        "scope": ",".join(META_SCOPES),
        "response_type": "code",
    }
    return ("https://www.facebook.com/v21.0/dialog/oauth?"
            + urllib.parse.urlencode(params))


def meta_exchange(code):
    """Code -> short token -> long-lived token (60 days)."""
    r = requests.get(
        f"https://graph.facebook.com/{config.META_API_VERSION}/oauth/access_token",
        params={
            "client_id": META_APP_ID,
            "client_secret": META_APP_SECRET,
            "redirect_uri": f"{BASE_URL}/connect/meta/callback",
            "code": code,
        }, timeout=30,
    )
    r.raise_for_status()
    short = r.json()["access_token"]

    r = requests.get(
        f"https://graph.facebook.com/{config.META_API_VERSION}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": META_APP_ID,
            "client_secret": META_APP_SECRET,
            "fb_exchange_token": short,
        }, timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    expires = _now() + timedelta(seconds=data.get("expires_in", 5184000))
    return data["access_token"], expires.isoformat()


def meta_list_pages(token):
    """Pages the user admins, with linked IG accounts.

    Page tokens never expire while the user token is valid, so we store
    the page token rather than the user token.
    """
    r = requests.get(
        f"https://graph.facebook.com/{config.META_API_VERSION}/me/accounts",
        params={
            "access_token": token,
            "fields": "id,name,access_token,"
                      "instagram_business_account{id,username}",
            "limit": 100,
        }, timeout=30,
    )
    r.raise_for_status()
    out = []
    for p in r.json().get("data", []):
        ig = p.get("instagram_business_account") or {}
        out.append({
            "id": p["id"],
            "name": p.get("name", p["id"]),
            "page_token": p.get("access_token"),
            "ig_id": ig.get("id"),
            "ig_username": ig.get("username"),
        })
    return out


# ------------------------------------------------------------------
# Google
# ------------------------------------------------------------------

def google_auth_url(state):
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": f"{BASE_URL}/connect/google/callback",
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",          # force refresh_token on repeat connects
        "state": state,
        "include_granted_scopes": "true",
    }
    return ("https://accounts.google.com/o/oauth2/v2/auth?"
            + urllib.parse.urlencode(params))


def google_exchange(code):
    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": f"{BASE_URL}/connect/google/callback",
            "grant_type": "authorization_code",
        }, timeout=30,
    )
    r.raise_for_status()
    d = r.json()
    expires = _now() + timedelta(seconds=d.get("expires_in", 3600))
    return d["access_token"], d.get("refresh_token"), expires.isoformat()


def google_token(conn, account):
    """Return a valid access token, refreshing if needed."""
    row = get_connection(conn, account, "google")
    if not row:
        return None

    expires_at = row["expires_at"]
    if expires_at and datetime.fromisoformat(expires_at) > _now() + timedelta(minutes=2):
        return row["access_token"]

    if not row["refresh_token"]:
        return row["access_token"]

    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "refresh_token": row["refresh_token"],
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "grant_type": "refresh_token",
        }, timeout=30,
    )
    if r.status_code != 200:
        save_connection(conn, account, "google",
                        last_error="Google access expired — reconnect needed")
        return None
    d = r.json()
    expires = (_now() + timedelta(seconds=d.get("expires_in", 3600))).isoformat()
    save_connection(conn, account, "google",
                    access_token=d["access_token"], expires_at=expires,
                    last_error=None)
    return d["access_token"]


def google_list_properties(token):
    """Everything the user can read, so the picker needs no typing."""
    sites, ga4 = [], []

    r = requests.get(
        "https://www.googleapis.com/webmasters/v3/sites",
        headers={"Authorization": f"Bearer {token}"}, timeout=30,
    )
    if r.status_code == 200:
        for s in r.json().get("siteEntry", []):
            if s.get("permissionLevel") != "siteUnverifiedUser":
                sites.append({
                    "url": s["siteUrl"],
                    "label": s["siteUrl"].replace("sc-domain:", ""),
                })

    r = requests.get(
        "https://analyticsadmin.googleapis.com/v1beta/accountSummaries",
        headers={"Authorization": f"Bearer {token}"},
        params={"pageSize": 200}, timeout=30,
    )
    if r.status_code == 200:
        for acct in r.json().get("accountSummaries", []):
            for p in acct.get("propertySummaries", []):
                ga4.append({
                    "id": p["property"].split("/")[-1],
                    "label": p.get("displayName", p["property"]),
                })

    return sites, ga4


# ------------------------------------------------------------------
# Invites — for when the person onboarding lacks the credentials
# ------------------------------------------------------------------

def create_invite(conn, account, provider):
    token = secrets.token_urlsafe(24)
    conn.execute(
        "INSERT INTO invite (token, account, provider, created_at) "
        "VALUES (?, ?, ?, ?)",
        (token, account, provider, _now().isoformat()),
    )
    conn.commit()
    return f"{BASE_URL}/invite/{token}"


def resolve_invite(conn, token):
    return conn.execute(
        "SELECT * FROM invite WHERE token = ? AND used_at IS NULL",
        (token,),
    ).fetchone()


def consume_invite(conn, token):
    conn.execute("UPDATE invite SET used_at = ? WHERE token = ?",
                 (_now().isoformat(), token))
    conn.commit()
