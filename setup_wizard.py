"""Guided one-time provider registration.

    python run.py --account bty setup

Every source here has a failure mode that returns empty rather than
erroring, so no step is marked done because you said it was. Each one
ends with a live call that proves the credential works, and the wizard
refuses to move on until it does.

Values are written to `.env` — these are application credentials for this
installation, not per-account tokens. Per-account tokens live in the
`connection` table and arrive through `run.py connect`.

Nothing here is destructive: an existing `.env` is read, updated key by
key, and rewritten with everything it already had.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import config

ENV_PATH = config.ROOT / ".env"

TICK, CROSS, DOT = "✓", "✗", "·"


# ------------------------------------------------------------------
# Terminal helpers
# ------------------------------------------------------------------

def head(n, title):
    print(f"\n\033[1m{n}. {title}\033[0m")


def step(text):
    print(f"   {DOT} {text}")


def ok(text):
    print(f"   \033[32m{TICK}\033[0m {text}")


def bad(text):
    print(f"   \033[31m{CROSS}\033[0m {text}")


def field(label, value):
    """Something to copy. Kept on its own line so double-click selects it."""
    print(f"\n     \033[1m{label}\033[0m")
    print(f"     {value}\n")


def ask(prompt, secret=False, allow_blank=False):
    while True:
        try:
            value = input(f"   {prompt}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n   stopped\n")
            sys.exit(1)
        if value or allow_blank:
            return value
        print("   (nothing entered)")


def confirm(prompt):
    try:
        return input(f"   {prompt} [Enter to continue, s to skip]: "
                     ).strip().lower() != "s"
    except (EOFError, KeyboardInterrupt):
        print("\n   stopped\n")
        sys.exit(1)


def open_url(url):
    print(f"\n     opening {url}")
    try:
        subprocess.run(["open", url], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


# ------------------------------------------------------------------
# .env
# ------------------------------------------------------------------

def read_env():
    if not ENV_PATH.exists():
        return {}
    out = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def write_env(updates):
    """Update keys in place, keeping comments and ordering intact."""
    existing = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    lines = existing.splitlines()
    seen = set()

    for i, line in enumerate(lines):
        m = re.match(r"^\s*([A-Z0-9_]+)\s*=", line)
        if m and m.group(1) in updates:
            key = m.group(1)
            lines[i] = f"{key}={updates[key]}"
            seen.add(key)

    for key, value in updates.items():
        if key not in seen:
            lines.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    os.chmod(ENV_PATH, 0o600)
    for key, value in updates.items():
        os.environ[key] = value


# ------------------------------------------------------------------
# Meta
# ------------------------------------------------------------------

def verify_meta(app_id, app_secret):
    """An app access token proves the id and secret are a real pair."""
    import requests
    r = requests.get(
        f"https://graph.facebook.com/{config.META_API_VERSION}/oauth/access_token",
        params={"client_id": app_id, "client_secret": app_secret,
                "grant_type": "client_credentials"},
        timeout=30,
    )
    if r.status_code == 200 and "access_token" in r.json():
        return True, "app id and secret are a valid pair"
    try:
        msg = r.json()["error"]["message"]
    except (ValueError, KeyError):
        msg = r.text[:200]
    return False, msg


def setup_meta(env):
    head(1, "Meta app — for Facebook and Instagram")

    if env.get("META_APP_ID") and env.get("META_APP_SECRET"):
        good, msg = verify_meta(env["META_APP_ID"], env["META_APP_SECRET"])
        if good:
            ok(f"already configured — {msg}")
            return True
        bad(f"stored credentials no longer work — {msg}")

    print("""
   You need your own Meta app. It is free, takes about five minutes, and
   needs no App Review while you are an admin of the pages you connect.
   App Review only matters when someone outside your app's user list
   needs to connect.""")

    if not confirm("Ready"):
        return False

    open_url("https://developers.facebook.com/apps/create/")
    step("Create app → type \033[1mBusiness\033[0m → name it anything")
    step("In the new app: Add product → \033[1mFacebook Login\033[0m → Web")
    step("Facebook Login → Settings → paste this as a Valid OAuth "
         "Redirect URI:")
    field("Redirect URI", f"{config.BASE_URL}/connect/meta/callback")
    step("App settings → Basic → copy the App ID and App Secret")

    app_id = ask("App ID")
    app_secret = ask("App Secret", secret=True)

    good, msg = verify_meta(app_id, app_secret)
    if not good:
        bad(f"that pair was rejected: {msg}")
        return False

    write_env({"META_APP_ID": app_id, "META_APP_SECRET": app_secret})
    ok("verified against the Graph API and saved")
    return True


# ------------------------------------------------------------------
# Google
# ------------------------------------------------------------------

GOOGLE_APIS = [
    ("Google Search Console API", "searchconsole.googleapis.com"),
    ("Google Analytics Data API", "analyticsdata.googleapis.com"),
    ("Google Analytics Admin API", "analyticsadmin.googleapis.com"),
    ("Gmail API", "gmail.googleapis.com"),
]

DELEGATION_SCOPES = ",".join([
    "https://www.googleapis.com/auth/webmasters.readonly",
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
])


def _service_account_identity(path):
    """Return (client_email, client_id) from the key file."""
    import json
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, None, f"could not read {path}: {e}"
    if data.get("type") != "service_account":
        return None, None, "that JSON is not a service account key"
    return data.get("client_email"), data.get("client_id"), None


def verify_google(key_path, subject):
    """Impersonate the user and list their Search Console properties.

    This is the only check that proves delegation is actually authorised.
    Reading the key file proves nothing — the failure mode being guarded
    against is a key that loads fine and returns an empty list because
    nobody completed the Admin console step.
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    try:
        creds = service_account.Credentials.from_service_account_file(
            key_path,
            scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
        ).with_subject(subject)
        service = build("searchconsole", "v1", credentials=creds,
                        cache_discovery=False)
        sites = service.sites().list().execute().get("siteEntry", [])
    except Exception as e:
        text = str(e)
        if "unauthorized_client" in text:
            return False, ("delegation not authorised — the Admin console "
                           "step has not been completed, or the scopes "
                           "there do not match")
        if "invalid_grant" in text:
            return False, (f"could not impersonate {subject} — check the "
                           f"address is a real user on this Workspace")
        return False, text[:300]

    if not sites:
        return False, (f"delegation works but {subject} has no Search "
                       f"Console properties — check you are signed in as "
                       f"the right person")
    return True, f"impersonating {subject}, {len(sites)} propert" \
                 f"{'y' if len(sites) == 1 else 'ies'} visible"


def setup_google(env):
    head(2, "Google service account — Search Console, Analytics, Gmail")

    key_path = env.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    subject = env.get("GOOGLE_IMPERSONATE", "")

    if key_path and subject and Path(key_path).exists():
        good, msg = verify_google(key_path, subject)
        if good:
            ok(f"already configured — {msg}")
            return True
        bad(f"stored setup does not work — {msg}")

    print("""
   A service account rather than a sign-in button, deliberately.

   An OAuth app in Testing status issues refresh tokens that expire after
   seven days, and Gmail's scope is restricted enough that leaving
   Testing needs a third-party security audit. You would be re-consenting
   weekly, forever.

   A service account with domain-wide delegation never expires. Better
   still, because it impersonates you it inherits your own access — so
   there is no step where you add an email to each property by hand.""")

    if not confirm("Ready"):
        return False

    open_url("https://console.cloud.google.com/projectcreate")
    step("Create a project (or pick one you already have)")

    print()
    for name, host in GOOGLE_APIS:
        step(f"Enable \033[1m{name}\033[0m")
    field("All four, one page each",
          "https://console.cloud.google.com/apis/library")

    open_url("https://console.cloud.google.com/iam-admin/serviceaccounts/create")
    step("Create a service account. No roles needed — it gets its access "
         "by impersonation, not by IAM.")
    step("Open it → Keys → Add key → Create new key → \033[1mJSON\033[0m")
    step("Save the file somewhere sensible. It is a credential; treat it "
         "like a password.")

    key_path = ask("Path to the JSON key")
    key_path = str(Path(key_path).expanduser().resolve())

    email, client_id, err = _service_account_identity(key_path)
    if err:
        bad(err)
        return False
    ok(f"key file reads as {email}")

    print("""
   Last step, and the one that does the real work. This authorises the
   service account to act as users on your Workspace.""")

    open_url("https://admin.google.com/ac/owl/domainwidedelegation")
    step("Admin console → Security → Access and data control → "
         "API controls → Domain-wide delegation → Add new")
    field("Client ID", client_id)
    field("OAuth scopes (paste as one line)", DELEGATION_SCOPES)
    step("Authorise. It can take a minute to take effect.")

    subject = ask("Your Workspace email address")

    good, msg = verify_google(key_path, subject)
    if not good:
        bad(msg)
        print("\n   If you only just authorised it, wait a minute and run "
              "setup again — the grant takes a moment to propagate.\n")
        return False

    write_env({
        "GOOGLE_APPLICATION_CREDENTIALS": key_path,
        "GOOGLE_IMPERSONATE": subject,
        "GMAIL_USER": subject,
    })
    ok(f"verified — {msg}")
    return True


# ------------------------------------------------------------------
# Anthropic
# ------------------------------------------------------------------

def verify_anthropic(key):
    import anthropic
    try:
        anthropic.Anthropic(api_key=key).models.list(limit=1)
        return True, "key accepted"
    except Exception as e:
        return False, str(e)[:200]


def setup_anthropic(env):
    head(3, "Anthropic API key — for claim extraction and briefs")

    key = env.get("ANTHROPIC_API_KEY", "")
    if key:
        good, msg = verify_anthropic(key)
        if good:
            ok(f"already configured — {msg}")
            return True
        bad(f"stored key does not work — {msg}")

    open_url("https://console.anthropic.com/settings/keys")
    key = ask("Anthropic API key", secret=True)

    good, msg = verify_anthropic(key)
    if not good:
        bad(msg)
        return False

    write_env({"ANTHROPIC_API_KEY": key})
    ok("verified and saved")
    return True


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def run(account):
    print(f"""
\033[1mAngle Engine setup\033[0m — one time, for this machine

Three things to register. Each ends with a live call that proves the
credential works, because every one of these has a failure mode that
returns nothing rather than an error.

Per-source connections (which page, which label, which folder) come
afterwards, from `python run.py --account {account} connect`.""")

    env = read_env()
    results = [
        ("Meta", setup_meta(env)),
        ("Google", setup_google(read_env())),
        ("Anthropic", setup_anthropic(read_env())),
    ]

    print("\n" + "─" * 60)
    for name, good in results:
        (ok if good else bad)(name)

    if all(good for _, good in results):
        print(f"\nAll verified. Next:\n\n    "
              f"python run.py --account {account} connect\n")
        return 0

    print("\nFinish the unverified steps and run setup again — it skips "
          "what already works.\n")
    return 1
