# CLAUDE.md

Orientation for Claude Code working in this repo.

## What this is

A diagnostic tool that reads a business's own organic footprint — social
posts and comments, search queries, call transcripts, reviews — and
produces a ranked bank of advertising angles, each with a script and
shot direction.

**It does not generate ads.** That is a product decision, not an
oversight. See "Decisions that must not be undone" below.

Owner: Chris, Oslo. Solo build. Norwegian SMB clients.

## Status

| Part | State |
|---|---|
| Schema, scoring, env loading | tested (`tests/test_scoring.py`) |
| Onboarding flow, OAuth wiring, invites | tested (`tests/test_onboard.py`) |
| Pre-flight checks (`doctor`) | tested (`tests/test_doctor.py`) |
| Brand token drift | tested (`tests/test_brand.py`) |
| Meta / Google / first-party API adapters | **never run against live endpoints** |
| Claim extraction + clustering | written, never run with a real key |
| Brief generation | written, never run with a real key |

Assume the API adapters have bugs. They were written against
documentation, not against responses. The most likely breakage is
Instagram insights, where metric names shift between API versions.
`doctor` now names the specific metrics that came back missing, and
`_ig_insights` records each drop in `pull_log` instead of swallowing it —
but the underlying fragility is still there and only real data will show
where.

## Run it

```bash
uv venv --python 3.12 && uv pip install -r requirements.txt
cp env.example .env             # fill in

python run.py --account self doctor          # ALWAYS first
python run.py --account self pull
python run.py --account self claims --limit 200
python run.py --account self score --top 20
python run.py --account self brief --top 5

python onboard.py --account acme    # client onboarding on :5000

for t in tests/test_*.py; do python "$t" || break; done
```

`doctor` comes first because Google answers a wrongly identified property
with an empty result set rather than an error — a misconfigured pull
looks exactly like a successful one that found nothing.

The rest of the stages are separate because `claims` is the only one that
costs money. Pull once, extract once, re-score for free while tuning
weights.

The example env file is `env.example`, without a leading dot, because a
local permission rule blocks writing paths matching `.env*`. Rename it if
that isn't a constraint for you.

## Architecture

```
run.py           CLI: doctor → pull → claims → score → brief
doctor.py        pre-flight credential probes
onboard.py       Flask onboarding server
auth.py          OAuth flows, per-account credential store
config.py        env loading (./.env, then $ANGLE_ENV_FILE, then real env)
db.py            sqlite helpers
schema.sql       signal → claim → cluster → score → brief

sources/
  meta.py        Page + IG posts, insights, and comment threads
  google.py      GSC queries, GA4 pages + site-search terms
  firstparty.py  call transcripts, email subject CSV, Trustpilot

pipeline/
  claims.py      LLM claim extraction + canonical clustering
  score.py       ranking
  brief.py       angle → script + shot direction, markdown render
  render_html.py the same brief set, Caldera-branded, for handing over

brand/
  BRAND.md       what Caldera is and where it applies
  tokens.css     the values. read at render time, never restated
```

Data flow: every source normalizes into one `signal` table. Claims are
extracted from signal text, clustered into canonical angles, scored,
then briefed.

## Decisions that must not be undone

These look like things worth "improving". They are not. Each one exists
for a reason that isn't obvious from the code.

**1. No ad generation.** The product outputs filming briefs, never
finished assets. Every competitor that tried automated generation either
died or pivoted to human-led services, and Meta announced native
brand-aware generation in Oct 2025 — that layer is going to free. The
decision layer is the business. Do not add an image or video generator.

**2. Cluster by claim, not by post.** A post is a container; a claim is
"I was worried it wouldn't fit". The signal is the same claim recurring
across a caption, four comments, a search query and a transcript. Do not
refactor toward per-post analysis.

**3. Saves and shares are scored separately and never summed.** A save
means "useful to me", a share means "this says something about me". They
map to different angle types. Averaging them destroys the distinction.

**4. Source diversity outweighs raw volume.** A claim in three sources
beats one appearing ten times in a single comment thread, because ten
mentions in one thread is usually one loud argument.

**5. Objections get an explicit scoring bonus.** Cold audiences hold the
objection and almost no ad addresses it.

**6. Boosted posts are excluded from engagement scoring** but their text
still feeds claim extraction. Their metrics blend organic and paid.

**7. OAuth for clients, service accounts only for self-runs.** A service
account makes the client add an email inside two Google consoles by
hand, with no error feedback — the highest-abandon step in onboarding.
Do not "simplify" back to service accounts.

**8. Delegation links stay.** The commonest reason onboarding dies is
that the person clicking doesn't hold the credentials. Every connect
step offers a single-use invite for whoever does.

**9. Payoff before the second ask.** Meta connects first and syncs
immediately so real counts are on screen before anything else is
requested. Do not reorder the onboarding steps or batch the asks.

## Conventions

- Plain Python, no framework beyond Flask for the onboarding server.
- sqlite for now. Swap to Postgres by changing `db.connect()` and
  `AUTOINCREMENT` → `SERIAL`; nothing else is sqlite-specific.
- API failures in one source must not kill a run — `run.py` catches per
  source and reports.
- LLM output is always parsed through `_json_from()`, which tolerates
  fenced JSON.
- No dashboard. Output is a document — markdown to work from, HTML to
  hand over. Do not build an interface for browsing angle banks until at
  least five paying clients have asked.
- Every check gets tested in both directions. A guard that has only ever
  passed and a guard that is broken produce identical output; `test_brand`
  and `test_doctor` both assert the failing case explicitly, and
  `test_scoring`'s boosted-exclusion assertion exists because that line
  used to print PASS without asserting anything.

## Two design systems, on purpose

**The onboarding page** (`templates/base.html`) — cool paper `#F7F7F5`,
ink `#16191D`, teal `#0F6E63` **only** for connected state, clay
`#A8543C` only for errors. Colour carries state and nothing else. The
tally number is the one bold element; everything else stays quiet.
Ledger rows, not cards — cards would imply the sources are equivalent
choices, and they aren't: Meta is required, the rest are additive.

**The angle bank** (`brand/` + `pipeline/render_html.py`) — Caldera.
Warm limestone, molten orange, ultrabold compressed display type, flat
and shadowless. See `brand/BRAND.md`.

These are deliberately not the same system. The onboarding page's job is
to not spook someone midway through an OAuth flow; the angle bank's job
is to look like the thing they paid for. Don't unify them.

`brand/tokens.css` is the only place Caldera's values live.
`render_html.py` reads that file at render time rather than restating it,
and `tests/test_brand.py` fails if the renderer asks for a token the
brand file doesn't define — because `var(--gone)` resolves to nothing
rather than erroring, so drift here is silent by default.

## Priorities

1. Run `doctor`, then `pull`, against real BTY credentials and fix what
   breaks. Expect Instagram insights to need work.
2. ~~Add error surfacing to the source adapters~~ — done. Drops now land
   in `pull_log` rather than vanishing. Read that table after the first
   live pull; it is the fastest way to see what the adapters got wrong.
3. Run the full pipeline on Chris's own accounts and read the output.
4. **The cold test** (see `docs/build-order.md`). Nothing else matters
   until this runs.

On (3): a good-looking angle bank is not validation. The premise — that
organic-derived angles beat control on cold paid audiences — is unproven,
and the available evidence leans mildly against the naive version. What
step 3 answers is narrower: whether anything in the top ten *surprises*
you. If nothing does, the pipeline works and the product doesn't.

## Do not

- Add ad generation, image generation, or video generation.
- Build a client-facing dashboard.
- Add Reddit or competitor-ad scraping — that's a different, already
  crowded product.
- Scrape Glassdoor. Their terms forbid it, they litigate, and reviews
  carry personal data under GDPR.
- Assume a good-looking angle bank means the premise is validated.
