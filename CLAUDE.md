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

python run.py --account bty setup            # once per machine
python run.py --account bty connect          # once per account
python run.py --account bty doctor           # before every pull
python run.py --account self pull
python run.py --account self estimate        # what claims will cost
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
run.py           CLI: setup → connect → doctor → pull → claims → score → brief
setup_wizard.py  one-time provider registration, every step verified
doctor.py        pre-flight credential probes
onboard.py       Flask onboarding server
providers.py     one descriptor per source — add a source here, not in five files
auth.py          OAuth flows, per-account credential store
config.py        env loading (./.env, then $ANGLE_ENV_FILE, then real env)
db.py            sqlite helpers
schema.sql       signal → claim → cluster → score → brief

sources/
  meta.py        Page + IG posts, insights, and comment threads
  google.py      GSC queries, GA4 pages + site-search terms
  firstparty.py  call transcripts, email subject CSV, Trustpilot

sources/ (cont.)
  gmail.py       one label, impersonated service account, scrubbed
  happyscribe.py transcripts synced from a folder

pipeline/
  scrub.py       anonymise conversational text BEFORE it is stored
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

**7. OAuth for clients, a delegated service account for self-runs.** For
a client, a service account would mean adding an email inside two Google
consoles by hand with no error feedback — the highest-abandon step in
onboarding. Do not "simplify" client onboarding back to service accounts.

For your own accounts the reverse holds, for two reasons that are not
obvious. An OAuth app in Testing status issues refresh tokens that expire
after **seven days**, so self-runs would need re-consenting weekly
forever; and Gmail's scope is restricted enough that leaving Testing
requires a third-party security audit. A service account with
domain-wide delegation has neither problem, and because it impersonates
you it inherits your own property access — so the two-console step
disappears entirely rather than being tolerated.

**10. Gmail is bounded to one label, and scrubbed before storage.** Never
the whole mailbox, never a search query. A label is a deliberate decision
about which threads are customer conversations and is explicable to
anyone who asks what was ingested. `pipeline/scrub.py` strips names,
addresses, phone numbers, signature blocks and quoted chains on the way
in, so the raw body is never written down.

**12. A capped `claims` run samples across sources, never down the
table.** `--limit` exists so a first run can check output shape cheaply.
Taking the first N rows would defeat it: rows arrive in pull order, so
the sample would be one source. Since source diversity is the
highest-weighted scoring term, every claim would then share a source,
every diversity score would be identical, and the bank would be ranked on
nothing while looking entirely plausible. Do not "simplify" the sampler.

**13. `signal.claimed_at` means processed, not "has claims".** A signal
that legitimately yields nothing has no claim rows, so selecting work by
the absence of claims re-sends exactly that material on every future run.
The marker is set only when the API call succeeded *and* its response
parsed — an unparseable response looks identical to "nothing here", and
marking those would silently discard real signal.

**14. Canonical claims and briefs are Norwegian; verbatims never are.**
The same objection in two languages would otherwise cluster as two
angles, halving the recurrence count the ranking rests on. The verbatim
is the one thing in the output that is unarguably real — translating it
is the only way to make it not so.

**11. Quoted reply chains are stripped for a scoring reason, not only a
privacy one.** A five-message thread quotes itself, so the same sentence
would arrive five times — and recurrence across independent signals is
the largest single input to the ranking. Leaving quotes in would inflate
exactly the number the product turns on.

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
