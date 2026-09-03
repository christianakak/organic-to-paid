"""Environment loading and settings.

Load order, last one wins:

    1. ./.env
    2. the file at $ANGLE_ENV_FILE
    3. the real environment

So a sibling checkout's `.env` can be borrowed wholesale with
`ANGLE_ENV_FILE=../other/.env` while any single value is still
overridable inline without editing a file.

Values are pushed into `os.environ` as they load, because `auth.py` reads
its OAuth app credentials with bare `os.getenv` at import time. Importing
config first is what makes that work.
"""

import os
from pathlib import Path

ROOT = Path(__file__).parent


def _load_env_file(path, override):
    """Minimal dotenv reader. No dependency, no interpolation, no export."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and (override or key not in os.environ):
            os.environ[key] = value


# ./.env first, then $ANGLE_ENV_FILE, then whatever is already in the real
# environment — which is never overwritten, so an inline override wins.
_load_env_file(ROOT / ".env", override=False)
if os.getenv("ANGLE_ENV_FILE"):
    _load_env_file(os.environ["ANGLE_ENV_FILE"], override=False)


def _int(name, default):
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


# ------------------------------------------------------------------
# Storage and output
# ------------------------------------------------------------------

ANGLE_DB = os.getenv("ANGLE_DB", str(ROOT / "angle.db"))
OUT_DIR = Path(os.getenv("ANGLE_OUT_DIR", str(ROOT / "out")))
# sources/firstparty.py reads ANGLE_TRANSCRIPT_DIR from os.environ and
# returns zero rows when it is unset, so the default is written back into
# the environment rather than kept here. Without that, dropping files into
# the obvious directory would silently do nothing.
os.environ.setdefault(
    "ANGLE_TRANSCRIPT_DIR", str(ROOT / "data" / "transcripts")
)
TRANSCRIPT_DIR = Path(os.environ["ANGLE_TRANSCRIPT_DIR"])

# Onboarding server. Must be a real https URL in production — neither Meta
# nor Google will redirect to non-local http.
BASE_URL = os.getenv("ANGLE_BASE_URL", "http://localhost:5000")


# ------------------------------------------------------------------
# LLM
# ------------------------------------------------------------------

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Two settings rather than one because the two stages have different jobs.
# MODEL runs over every signal in the corpus and is the only stage that
# costs real money; BRIEF_MODEL runs over a handful of angles and is the
# judgment layer being sold. Both default to the same model — dropping the
# extraction tier to save money is a decision to make deliberately, with
# output to compare, not a default to inherit.
MODEL = os.getenv("ANGLE_MODEL", "claude-opus-5")
BRIEF_MODEL = os.getenv("ANGLE_BRIEF_MODEL", MODEL)

# Fixed vocabulary. claims.py falls back to "benefit" for anything the
# model returns that isn't in here, so adding a type means adding it here
# and in EXTRACT_SYSTEM, or it silently collapses.
CLAIM_TYPES = ("benefit", "objection", "identity", "proof", "use_case")


# ------------------------------------------------------------------
# Pull window and caps
# ------------------------------------------------------------------

LOOKBACK_DAYS = _int("ANGLE_LOOKBACK_DAYS", 540)      # ~18 months
MAX_POSTS = _int("ANGLE_MAX_POSTS", 500)
MAX_COMMENTS_PER_POST = _int("ANGLE_MAX_COMMENTS_PER_POST", 200)


# ------------------------------------------------------------------
# Meta
# ------------------------------------------------------------------

META_API_VERSION = os.getenv("META_API_VERSION", "v23.0")
META_TOKEN = os.getenv("META_TOKEN", "")
META_PAGE_ID = os.getenv("META_PAGE_ID", "")
META_IG_USER_ID = os.getenv("META_IG_USER_ID", "")


# ------------------------------------------------------------------
# Google
# ------------------------------------------------------------------

# Service-account JSON, for running the tool on properties you own.
# Clients go through OAuth instead — see auth.py.
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")

# A domain property is "sc-domain:example.com", not a URL. A URL-prefix
# property is the exact prefix including protocol and trailing slash.
# Getting this wrong returns empty results rather than an error, which is
# why `run.py doctor` exists.
GSC_SITE_URL = os.getenv("GSC_SITE_URL", "")

# The numeric property ID from GA4 Admin, not the G-XXXX measurement ID.
GA4_PROPERTY_ID = os.getenv("GA4_PROPERTY_ID", "")


# ------------------------------------------------------------------
# First-party
# ------------------------------------------------------------------

# sources/firstparty.py reads these straight from os.getenv rather than
# from here, which works because loading this module populates os.environ.
# Mirrored as attributes anyway so `doctor` can report on them.
EMAIL_CSV = os.getenv("ANGLE_EMAIL_CSV", "")
TRUSTPILOT_API_KEY = os.getenv("TRUSTPILOT_API_KEY", "")
TRUSTPILOT_BUSINESS_UNIT_ID = os.getenv("TRUSTPILOT_BUSINESS_UNIT_ID", "")
