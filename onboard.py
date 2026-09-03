#!/usr/bin/env python3
"""Onboarding server.

    python onboard.py --account acme
    -> http://localhost:5000

Flow design, in order of how much each decision reduces abandonment:

1. OAuth, not service accounts. One button instead of pasting an email
   into two Google consoles.

2. Payoff before the second ask. Meta connects first and the sync runs
   immediately, so real counts are on screen before anything else is
   requested. Every later ask happens with evidence already visible.

3. Delegation. The commonest reason onboarding dies is that the person
   clicking doesn't hold the credentials — the owner says yes, opens the
   Google step, and finds their web agency has the access. Every step
   has a "send this to whoever manages it" link that hands off that one
   step without restarting anything.

4. Nothing after step one is required. Optional sources say so, and the
   run works without them.
"""

import argparse
import secrets
import threading

from flask import (Flask, redirect, render_template, request,
                   session, url_for, jsonify)

import auth
import config
import db

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)
ACCOUNT = "demo"

_sync_state = {}


# ------------------------------------------------------------------
# Status
# ------------------------------------------------------------------

def _status(account):
    with db.connect() as conn:
        meta = auth.get_connection(conn, account, "meta")
        google = auth.get_connection(conn, account, "google")
        counts = {}
        for source, kind, n in db.counts(conn, account):
            counts[f"{source}:{kind}"] = n

    posts = sum(v for k, v in counts.items()
                if k.startswith("meta") and k.endswith("post"))
    comments = sum(v for k, v in counts.items()
                   if k.startswith("meta") and k.endswith("comment"))
    queries = sum(v for k, v in counts.items()
                  if k.startswith(("gsc", "ga4")) and k.endswith("query"))
    transcripts = counts.get("transcript:comment", 0)
    reviews = counts.get("trustpilot:comment", 0)

    return {
        "meta": {
            "connected": bool(meta and meta["meta_page_id"]),
            "needs_picker": bool(meta and not meta["meta_page_id"]),
            "error": meta["last_error"] if meta else None,
            "posts": posts,
            "comments": comments,
        },
        "google": {
            "connected": bool(google and (google["gsc_site_url"]
                                          or google["ga4_property_id"])),
            "needs_picker": bool(google and not (google["gsc_site_url"]
                                                 or google["ga4_property_id"])),
            "error": google["last_error"] if google else None,
            "queries": queries,
        },
        "transcripts": transcripts,
        "reviews": reviews,
        "total": posts + comments + queries + transcripts + reviews,
        "syncing": _sync_state.get(account, {}).get("running", False),
    }


@app.route("/")
def index():
    return render_template("onboard.html", account=ACCOUNT,
                           status=_status(ACCOUNT))


@app.route("/status")
def status_json():
    return jsonify(_status(ACCOUNT))


# ------------------------------------------------------------------
# Meta
# ------------------------------------------------------------------

@app.route("/connect/meta")
def connect_meta():
    state = secrets.token_urlsafe(16)
    session["state"] = state
    session["account"] = request.args.get("account", ACCOUNT)
    return redirect(auth.meta_auth_url(state))


@app.route("/connect/meta/callback")
def meta_callback():
    if request.args.get("state") != session.get("state"):
        return render_template("error.html",
                               message="That link expired. Start again from "
                                       "the beginning and it'll work."), 400
    if request.args.get("error"):
        return redirect(url_for("index"))

    account = session.get("account", ACCOUNT)
    token, expires = auth.meta_exchange(request.args["code"])
    with db.connect() as conn:
        auth.save_connection(conn, account, "meta", access_token=token,
                             expires_at=expires,
                             scopes=",".join(auth.META_SCOPES),
                             last_error=None)
    return redirect(url_for("pick_meta"))


@app.route("/connect/meta/pick", methods=["GET", "POST"])
def pick_meta():
    account = session.get("account", ACCOUNT)
    with db.connect() as conn:
        row = auth.get_connection(conn, account, "meta")
        if not row:
            return redirect(url_for("index"))

        if request.method == "POST":
            page_id = request.form["page_id"]
            pages = auth.meta_list_pages(row["access_token"])
            chosen = next((p for p in pages if p["id"] == page_id), None)
            if chosen:
                auth.save_connection(
                    conn, account, "meta",
                    access_token=chosen["page_token"] or row["access_token"],
                    meta_page_id=chosen["id"],
                    meta_ig_user_id=chosen["ig_id"],
                )
            _start_sync(account)
            return redirect(url_for("index"))

        pages = auth.meta_list_pages(row["access_token"])

    if len(pages) == 1:
        # Don't make someone choose from a list of one.
        with db.connect() as conn:
            p = pages[0]
            auth.save_connection(conn, account, "meta",
                                 access_token=p["page_token"],
                                 meta_page_id=p["id"],
                                 meta_ig_user_id=p["ig_id"])
        _start_sync(account)
        return redirect(url_for("index"))

    return render_template("pick_meta.html", pages=pages, account=account)


# ------------------------------------------------------------------
# Google
# ------------------------------------------------------------------

@app.route("/connect/google")
def connect_google():
    state = secrets.token_urlsafe(16)
    session["state"] = state
    session["account"] = request.args.get("account", ACCOUNT)
    return redirect(auth.google_auth_url(state))


@app.route("/connect/google/callback")
def google_callback():
    if request.args.get("state") != session.get("state"):
        return render_template("error.html",
                               message="That link expired. Start again from "
                                       "the beginning and it'll work."), 400
    if request.args.get("error"):
        return redirect(url_for("index"))

    account = session.get("account", ACCOUNT)
    access, refresh, expires = auth.google_exchange(request.args["code"])
    with db.connect() as conn:
        auth.save_connection(conn, account, "google", access_token=access,
                             refresh_token=refresh, expires_at=expires,
                             scopes=" ".join(auth.GOOGLE_SCOPES),
                             last_error=None)
    return redirect(url_for("pick_google"))


@app.route("/connect/google/pick", methods=["GET", "POST"])
def pick_google():
    account = session.get("account", ACCOUNT)
    with db.connect() as conn:
        token = auth.google_token(conn, account)
        if not token:
            return redirect(url_for("index"))

        if request.method == "POST":
            auth.save_connection(
                conn, account, "google",
                gsc_site_url=request.form.get("site") or None,
                ga4_property_id=request.form.get("property") or None,
            )
            _start_sync(account)
            return redirect(url_for("index"))

        sites, properties = auth.google_list_properties(token)

    if len(sites) <= 1 and len(properties) <= 1:
        with db.connect() as conn:
            auth.save_connection(
                conn, account, "google",
                gsc_site_url=sites[0]["url"] if sites else None,
                ga4_property_id=properties[0]["id"] if properties else None,
            )
        _start_sync(account)
        return redirect(url_for("index"))

    return render_template("pick_google.html", sites=sites,
                           properties=properties, account=account)


# ------------------------------------------------------------------
# Delegation
# ------------------------------------------------------------------

@app.route("/delegate/<provider>")
def delegate(provider):
    account = session.get("account", ACCOUNT)
    with db.connect() as conn:
        link = auth.create_invite(conn, account, provider)
    label = {"meta": "Facebook and Instagram",
             "google": "Google Analytics and Search Console"}[provider]
    return render_template("delegate.html", link=link, label=label,
                           provider=provider)


@app.route("/invite/<token>")
def invite(token):
    with db.connect() as conn:
        row = auth.resolve_invite(conn, token)
        if not row:
            return render_template(
                "error.html",
                message="This link has already been used. Ask whoever sent "
                        "it for a new one."), 404
        auth.consume_invite(conn, token)
    session["account"] = row["account"]
    session["invited"] = True
    return redirect(url_for(f"connect_{row['provider']}",
                            account=row["account"]))


# ------------------------------------------------------------------
# Background sync
# ------------------------------------------------------------------

def _start_sync(account):
    if _sync_state.get(account, {}).get("running"):
        return
    _sync_state[account] = {"running": True}

    def work():
        try:
            from sources import meta, google, firstparty
            with db.connect() as conn:
                for mod in (meta, google, firstparty):
                    try:
                        mod.pull(conn, account)
                    except Exception as e:
                        print(f"sync: {mod.__name__} failed — {e}")
                conn.commit()
        finally:
            _sync_state[account] = {"running": False}

    threading.Thread(target=work, daemon=True).start()


@app.route("/sync", methods=["POST"])
def sync():
    _start_sync(session.get("account", ACCOUNT))
    return jsonify({"started": True})


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--account", default="demo")
    p.add_argument("--port", type=int, default=5000)
    args = p.parse_args()

    global ACCOUNT
    ACCOUNT = args.account
    db.init()
    print(f"\n  Onboarding for '{ACCOUNT}' → http://localhost:{args.port}\n")
    app.run(port=args.port, debug=False)


if __name__ == "__main__":
    main()
