-- Angle Engine schema.
--
-- The shape is signal -> claim -> cluster -> score -> brief.
--
-- Every source normalises into `signal`. A signal is a container: a post,
-- a comment, a search query, a landing page, a transcript turn. Claims are
-- extracted from signal text and clustered into canonical angles. Scoring
-- ranks the clusters. Briefs are written from the top of that ranking.
--
-- The two tables at the bottom (connection, invite) belong to the
-- onboarding server rather than the pipeline, and are only touched by
-- auth.py and onboard.py.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;


-- ------------------------------------------------------------------
-- signal — one normalised row per thing anybody said or did
-- ------------------------------------------------------------------
--
-- `kind` is the shape of the thing, not its origin:
--   post     a broadcast by the brand (Meta post, IG media, email subject)
--   comment  a customer talking (Meta comment, transcript turn, review)
--   query    something typed into a search box (GSC, GA4 site search)
--   page     a landing page with behaviour attached (GA4)
--
-- Engagement columns are nullable on purpose. A null means "this source
-- does not report this metric"; a zero means "it reported zero". Collapsing
-- the two would make save/share rates lie, so sources.meta._zero_to_none
-- converts the API's placeholder zeroes back to null before they land here.

CREATE TABLE IF NOT EXISTS signal (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account       TEXT    NOT NULL,
    source        TEXT    NOT NULL,   -- meta_page | meta_ig | gsc | ga4
                                      -- transcript | email | trustpilot
    kind          TEXT    NOT NULL,   -- post | comment | query | page
    text          TEXT    NOT NULL DEFAULT '',

    external_id   TEXT,               -- provider id, or a synthesised key
    parent_id     TEXT,               -- post id for a comment, file for a turn
    permalink     TEXT,
    created_at    TEXT,               -- ISO8601, as the provider gave it

    reach         INTEGER,            -- unique people
    impressions   INTEGER,            -- total views / sessions / sends
    saves         INTEGER,            -- "useful to me"
    shares        INTEGER,            -- "this says something about me"
    reactions     INTEGER,            -- likes, opens, review stars
    comment_count INTEGER,
    clicks        INTEGER,
    conversions   INTEGER,

    is_boosted    INTEGER NOT NULL DEFAULT 0,  -- paid money touched this;
                                               -- excluded from engagement
                                               -- scoring, text still mined
    raw           TEXT,               -- JSON blob of the provider payload
    fetched_at    TEXT    NOT NULL,

    -- Re-running `pull` must be idempotent. Every adapter counts how many
    -- rows it actually added by checking whether insert_signal returned an
    -- id, and that only works if a repeat insert is silently ignored here.
    UNIQUE (account, external_id)
);

CREATE INDEX IF NOT EXISTS idx_signal_account   ON signal (account);
CREATE INDEX IF NOT EXISTS idx_signal_acct_src  ON signal (account, source, kind);


-- ------------------------------------------------------------------
-- claim — a discrete thing being asserted, extracted from signal text
-- ------------------------------------------------------------------
--
-- `text` is the claim normalised into a sentence. `verbatim` is the
-- customer's own words, kept because the quotes are half of what a client
-- is actually paying for. `cluster_id` is null until clustering runs.

CREATE TABLE IF NOT EXISTS claim (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account     TEXT    NOT NULL,
    signal_id   INTEGER NOT NULL REFERENCES signal (id) ON DELETE CASCADE,
    text        TEXT    NOT NULL DEFAULT '',
    verbatim    TEXT    NOT NULL DEFAULT '',
    claim_type  TEXT    NOT NULL,   -- benefit | objection | identity
                                    -- proof | use_case
    cluster_id  INTEGER REFERENCES cluster (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_claim_account ON claim (account);
CREATE INDEX IF NOT EXISTS idx_claim_signal  ON claim (signal_id);
CREATE INDEX IF NOT EXISTS idx_claim_cluster ON claim (cluster_id);


-- ------------------------------------------------------------------
-- cluster — a canonical angle; the same claim said many ways
-- ------------------------------------------------------------------
--
-- pipeline/claims.py inserts with INSERT OR IGNORE and falls back to a
-- SELECT on (account, canonical), so that pair has to be unique.

CREATE TABLE IF NOT EXISTS cluster (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account     TEXT    NOT NULL,
    canonical   TEXT    NOT NULL,
    claim_type  TEXT    NOT NULL,
    UNIQUE (account, canonical)
);

CREATE INDEX IF NOT EXISTS idx_cluster_account ON cluster (account);


-- ------------------------------------------------------------------
-- score — the ranking, recomputed from scratch on every `score` run
-- ------------------------------------------------------------------
--
-- Re-scoring costs no API calls, so it is meant to be run repeatedly while
-- tuning weights. cluster_id is the primary key so INSERT OR REPLACE
-- overwrites rather than accumulating.

CREATE TABLE IF NOT EXISTS score (
    cluster_id           INTEGER PRIMARY KEY
                         REFERENCES cluster (id) ON DELETE CASCADE,
    recurrence           REAL,
    source_diversity     REAL,
    save_weight          REAL,
    share_weight         REAL,
    objection_bonus      REAL,
    conversion_proximity REAL,
    total                REAL
);


-- ------------------------------------------------------------------
-- brief — angle turned into something you can point a camera at
-- ------------------------------------------------------------------
--
-- Appended, never replaced. Briefs are rewritten as the corpus grows and
-- comparing this month's take against last month's is the point.

CREATE TABLE IF NOT EXISTS brief (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id INTEGER NOT NULL REFERENCES cluster (id) ON DELETE CASCADE,
    hook       TEXT,
    script     TEXT,
    scene      TEXT,
    talent     TEXT,
    props      TEXT,
    rationale  TEXT,
    testing    TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_brief_cluster ON brief (cluster_id);


-- ------------------------------------------------------------------
-- pull_log — what a pull dropped, and why
-- ------------------------------------------------------------------
--
-- The adapters were written against documentation rather than against real
-- responses, and their instinct is to degrade quietly. A pull that returns
-- zero rows because a property id is wrong looks identical to one that
-- returns zero because there is no data. This table is the difference.

CREATE TABLE IF NOT EXISTS pull_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    account TEXT NOT NULL,
    source  TEXT NOT NULL,
    ts      TEXT NOT NULL,
    event   TEXT NOT NULL,   -- ok | dropped | skipped | error
    detail  TEXT
);

CREATE INDEX IF NOT EXISTS idx_pull_log_account ON pull_log (account, ts);


-- ------------------------------------------------------------------
-- connection — per-account OAuth credentials and property selections
-- ------------------------------------------------------------------
--
-- The column list is fixed by the whitelist in auth.save_connection; adding
-- a column here without adding it there means writes to it are dropped.

CREATE TABLE IF NOT EXISTS connection (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    account          TEXT NOT NULL,
    provider         TEXT NOT NULL,   -- meta | google
    access_token     TEXT,
    refresh_token    TEXT,
    expires_at       TEXT,
    scopes           TEXT,
    meta_page_id     TEXT,
    meta_ig_user_id  TEXT,
    gsc_site_url     TEXT,
    ga4_property_id  TEXT,
    connected_at     TEXT,
    last_sync_at     TEXT,
    last_error       TEXT,

    -- Per-source choices as JSON: the Gmail label, the HappyScribe
    -- folder, the Trustpilot business unit. The four dedicated columns
    -- above predate this and still work, but adding a column per source
    -- forever does not scale — a seventh source should be a descriptor
    -- in providers.py and an adapter, not a migration.
    settings         TEXT,

    UNIQUE (account, provider)
);


-- ------------------------------------------------------------------
-- invite — single-use delegation links
-- ------------------------------------------------------------------
--
-- Onboarding usually dies because the person clicking does not hold the
-- credentials. An invite lets whoever does connect exactly one source and
-- land nowhere else. Single use: resolve_invite filters on used_at IS NULL.

CREATE TABLE IF NOT EXISTS invite (
    token      TEXT PRIMARY KEY,
    account    TEXT NOT NULL,
    provider   TEXT NOT NULL,
    created_at TEXT NOT NULL,
    used_at    TEXT
);
