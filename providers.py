"""One descriptor per data source.

Adding a source should be a descriptor here, an adapter in `sources/`,
and nothing else. Before this existed, a new source meant a column on
`connection`, a branch in the onboarding page, a branch in `doctor`, and
a line in `run.py` — four places to forget.

Two sources are deliberately not fully described by this: Meta and
Google. Both have real OAuth flows with provider-specific pickers (which
Facebook page, which Analytics property) and delegation links, and
pretending they are the same shape as "paste an API key" would make the
registry lie. They are listed here for ordering and labelling; their
flows stay in `auth.py` and `onboard.py`.

Everything else — HappyScribe, Trustpilot, and anything added later — is
fully driven from here.

## Why Google is a service account for self-runs

An OAuth app in Testing status hands out refresh tokens that expire after
seven days, and Google's restricted scopes (Gmail among them) additionally
need a third-party security audit before an app can leave Testing. Running
this on your own accounts through OAuth would mean re-consenting weekly,
indefinitely.

A service account with domain-wide delegation, impersonating a Workspace
user, has neither problem: it never expires, and because it inherits that
user's own access it needs no per-property grants at all. That deletes the
step the original design named as the highest-abandon one — adding an email
by hand inside two separate Google consoles, with no feedback when it is
wrong.

OAuth stays correct for clients, who are not on your Workspace. Same
adapters and same tables either way; only the door differs.
"""

# Order is the order sources appear on the onboarding page, and it is
# load-bearing. Meta is first because it connects fastest and its sync
# fires immediately, putting real counts on screen before anything else
# is asked for. Every later request then happens with evidence already
# visible. Do not reorder to group by "type".
PROVIDERS = [
    {
        "key": "meta",
        "label": "Facebook & Instagram",
        "blurb": "Posts, reach, and the comment threads underneath them.",
        "auth": "oauth",
        "required": True,
        "why": "Comments are the highest-signal text in the corpus and "
               "the part most tooling ignores.",
    },
    {
        "key": "google",
        "label": "Search Console & Analytics",
        "blurb": "The words people type before they find you.",
        "auth": "oauth",            # service_account for self-runs
        "required": False,
        "why": "Search queries are demand vocabulary — the customer's "
               "own phrasing, unmediated by your copy.",
    },
    {
        "key": "gmail",
        "label": "Gmail",
        "blurb": "One label you choose. Anonymised before anything is stored.",
        "auth": "service_account",
        "required": False,
        "needs": [
            {
                "field": "label",
                "prompt": "Which Gmail label holds customer threads?",
                "picker": "gmail_labels",
            },
        ],
        "why": "Customers writing to you directly, in their own words, "
               "at length. Objections in writing.",
        "caution": "Reading a mailbox is a far larger privacy surface "
                   "than post metrics. Bounded to one label, and "
                   "scrubbed of names, addresses, phone numbers, "
                   "signatures and quoted chains before it is stored.",
    },
    {
        "key": "happyscribe",
        "label": "Call transcripts",
        "blurb": "Pick a HappyScribe folder and it syncs.",
        "auth": "api_key",
        "required": False,
        "credential": {
            "field": "api_key",
            "prompt": "HappyScribe API key",
            "where": "happyscribe.com → account settings → API",
            "secret": True,
        },
        "needs": [
            {
                "field": "folder_id",
                "prompt": "Which folder?",
                "picker": "happyscribe_folders",
            },
        ],
        "why": "Rated the single highest-signal source: objections said "
               "out loud, with the rep's answer next to them.",
    },
    {
        "key": "trustpilot",
        "label": "Reviews",
        "blurb": "Paste your domain — the business unit is looked up for you.",
        "auth": "api_key",
        "required": False,
        "credential": {
            "field": "api_key",
            "prompt": "Trustpilot API key",
            "where": "developers.trustpilot.com → your application",
            "secret": True,
        },
        "needs": [
            {
                "field": "domain",
                "prompt": "Your domain, e.g. btygruppen.no",
                "resolver": "trustpilot_business_unit",
            },
        ],
        "why": "Reviews are written by people with a reason to be "
               "specific, which makes them unusually quotable.",
    },
]

BY_KEY = {p["key"]: p for p in PROVIDERS}

# Sources whose text is a person talking rather than the brand
# broadcasting. These get scrubbed at ingest — see pipeline/scrub.py.
CONVERSATIONAL = {"gmail", "happyscribe", "trustpilot", "meta"}


def get(key):
    return BY_KEY[key]


def api_key_providers():
    """The ones fully driven from this file: one paste, then a picker."""
    return [p for p in PROVIDERS if p["auth"] == "api_key"]


def needs(key):
    return BY_KEY[key].get("needs", [])
