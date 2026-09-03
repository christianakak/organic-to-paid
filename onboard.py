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
import providers

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
    emails = counts.get("gmail:comment", 0)

    with db.connect() as conn:
        gmail_set = auth.get_settings(conn, account, "gmail")
        hs_set = auth.get_settings(conn, account, "happyscribe")
        tp_set = auth.get_settings(conn, account, "trustpilot")
        # What the last pull actually dropped. A silent failure becomes
        # visible at the one moment someone is still looking at the
        # screen and can do something about it.
        drops = conn.execute(
            "SELECT source, detail FROM pull_log "
            "WHERE account = ? AND event IN ('dropped', 'error') "
            "ORDER BY id DESC LIMIT 5",
            (account,),
        ).fetchall()

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
        "gmail": {
            "connected": bool(gmail_set.get("label_id")),
            "label": gmail_set.get("label_name") or "",
            "messages": emails,
        },
        "happyscribe": {
            "connected": bool(hs_set.get("api_key")),
            "configured": bool(hs_set.get("folder_id")),
            "turns": transcripts,
        },
        "trustpilot": {
            "connected": bool(tp_set.get("api_key")),
            "configured": bool(tp_set.get("business_unit_id")),
            "name": tp_set.get("display_name") or "",
            "reviews": reviews,
        },
        "transcripts": transcripts,
        "reviews": reviews,
        "dropped": [{"source": d["source"], "detail": d["detail"]}
                    for d in drops],
        "total": (posts + comments + queries + transcripts + reviews
                  + emails),
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
# Gmail — one label, never the whole mailbox
# ------------------------------------------------------------------
#
# No OAuth button here. Gmail runs on the same delegated service account
# as Search Console and Analytics, so there is nothing to authorise at
# this point — only a label to choose. The bound is the product: a
# deliberate label selection is explicable to anyone who asks what was
# ingested, and a whole-mailbox sweep is not.

@app.route("/connect/gmail", methods=["GET", "POST"])
def pick_gmail():
    account = session.get("account", ACCOUNT)
    from sources import gmail

    with db.connect() as conn:
        if request.method == "POST":
            label_id = request.form.get("label_id") or None
            label_name = request.form.get("label_name") or ""
            if label_id:
                auth.update_settings(conn, account, "gmail",
                                     label_id=label_id,
                                     label_name=label_name)
                _start_sync(account)
            return redirect(url_for("index"))

        try:
            labels = gmail.list_labels(conn, account)
        except Exception as e:
            return render_template(
                "error.html",
                message=f"Couldn't read your Gmail labels. Usually this "
                        f"means domain-wide delegation hasn't been "
                        f"authorised yet — run setup again. ({e})"), 400

    return render_template("pick_gmail.html", labels=labels, account=account)


# ------------------------------------------------------------------
# API-key sources — driven entirely from providers.py
# ------------------------------------------------------------------
#
# One route for every source whose connect flow is "paste a key, then
# pick something". Adding a seventh source of this shape needs a
# descriptor and an adapter, and nothing here.

@app.route("/connect/<provider>/key", methods=["GET", "POST"])
def connect_key(provider):
    spec = providers.BY_KEY.get(provider)
    if not spec or spec["auth"] != "api_key":
        return render_template("error.html",
                               message="Unknown source."), 404

    account = session.get("account", ACCOUNT)

    if request.method == "POST":
        key = (request.form.get("api_key") or "").strip()
        good, message = _verify_key(provider, key)
        if not good:
            return render_template("connect_key.html", spec=spec,
                                   account=account, error=message), 400
        with db.connect() as conn:
            auth.save_connection(conn, account, provider,
                                 access_token=key, last_error=None)
            auth.update_settings(conn, account, provider, api_key=key)
        return redirect(url_for("configure_key", provider=provider))

    return render_template("connect_key.html", spec=spec, account=account,
                           error=None)


def _verify_key(provider, key):
    """Prove the key works before storing it. One call, cheapest endpoint."""
    if not key:
        return False, "Nothing pasted."
    try:
        if provider == "happyscribe":
            from sources import happyscribe
            happyscribe.verify(key)
            return True, "ok"
        if provider == "trustpilot":
            # Trustpilot has no cheap whoami; the domain lookup on the
            # next screen is what proves the key. Accept it here and let
            # that step reject it with a message that names the domain.
            return True, "ok"
    except Exception as e:
        return False, f"That key was rejected: {e}"
    return True, "ok"


@app.route("/connect/<provider>/configure", methods=["GET", "POST"])
def configure_key(provider):
    spec = providers.BY_KEY.get(provider)
    if not spec:
        return render_template("error.html", message="Unknown source."), 404

    account = session.get("account", ACCOUNT)

    with db.connect() as conn:
        settings = auth.get_settings(conn, account, provider)
        key = settings.get("api_key")

        if request.method == "POST":
            if provider == "trustpilot":
                from sources.firstparty import resolve_business_unit
                domain = request.form.get("domain", "")
                unit_id, label = resolve_business_unit(key, domain)
                if not unit_id:
                    return render_template(
                        "configure_key.html", spec=spec, account=account,
                        options=None, error=label), 400
                auth.update_settings(conn, account, provider,
                                     domain=domain,
                                     business_unit_id=unit_id,
                                     display_name=label)
            else:
                auth.update_settings(
                    conn, account, provider,
                    folder_id=request.form.get("folder_id") or None,
                )
            _start_sync(account)
            return redirect(url_for("index"))

        options = None
        if provider == "happyscribe":
            from sources import happyscribe
            try:
                options = happyscribe.list_folders(conn, account)
            except Exception:
                options = []

    return render_template("configure_key.html", spec=spec, account=account,
                           options=options, error=None)


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
            from sources import (meta, google, firstparty, gmail,
                                 happyscribe)
            with db.connect() as conn:
                for mod in (meta, google, gmail, happyscribe, firstparty):
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
