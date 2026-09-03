# Angle Engine

Mines a business's own organic footprint to produce a ranked bank of
advertising angles, each with a script and shot direction.

It does not generate ads. It tells you what to film.

---

## Setup

```bash
uv venv --python 3.12
uv pip install -r requirements.txt

python run.py --account bty setup      # once per machine
python run.py --account bty connect    # once per account
```

`setup` registers the two provider apps you need — a Meta app and a
Google service account — walking through the exact clicks with your
redirect URI and delegation scopes pre-filled. Every step ends with a
live call that proves the credential works, because all of these fail by
returning nothing rather than by erroring. It writes to `.env` and skips
anything already verified, so re-running it is safe.

`connect` opens a page where each source is one click or one paste.
Counts appear in the terminal as data lands.

**Google is a service account here, not a sign-in button, and that is
deliberate.** An OAuth app in Testing status issues refresh tokens that
expire after seven days, and Gmail's scope is restricted enough that
leaving Testing needs a third-party security audit — you would be
re-consenting weekly, indefinitely. A service account with domain-wide
delegation never expires, and because it impersonates you it inherits
your own property access, which removes the step where you add an email
by hand inside two Google consoles and get no feedback when it's wrong.
OAuth remains the right door for clients, who aren't on your Workspace.

Then, before every pull:

```bash
python run.py --account bty doctor
```

`doctor` probes every configured source and reads at least one real row
from each. It exists because of one specific failure: Google answers a
wrongly identified property with an empty result set rather than an
error, so a misconfigured pull reports success and stores nothing. It
also parses your first transcript and tells you whether it actually split
into speaker turns, which is the other failure that doesn't announce
itself.

### Borrowing config from a sibling checkout

Everything this needs from another project is environment variables and
some data files — filesystem proximity, not shared git history. Keep it
as a sibling directory and point at the neighbour's config:

```
~/code/
  leira/            # untouched
    .env
    data/
  angle-engine/     # own repo, own history
```

```bash
export ANGLE_ENV_FILE=../leira/.env
export ANGLE_TRANSCRIPT_DIR=../leira/data/transcripts
python run.py --account self pull
```

Real environment variables always beat the file, so you can override any
single value inline without editing anything.

**Meta token** needs: `pages_read_engagement`, `pages_show_list`,
`read_insights`, `instagram_basic`, `instagram_manage_insights`.
Use a long-lived Page token, not a user token.

### Google (GSC + GA4)

One service account covers both. The failure mode here is silent: if you
skip the granting steps the APIs return **empty results rather than an
error**, so you'll think the pull worked and got nothing.

1. In Google Cloud Console, create or pick a project.
2. Enable both **Google Search Console API** and **Google Analytics
   Data API**.
3. Create a service account, then create a JSON key for it. Save it as
   `service-account.json` and point `GOOGLE_APPLICATION_CREDENTIALS`
   at the path.
4. Copy the service account email — it looks like
   `something@project-id.iam.gserviceaccount.com`.
5. **Search Console** → your property → Settings → Users and permissions
   → add that email as a **Full** user. (Restricted works for reads but
   Full avoids surprises.)
6. **GA4** → Admin → Property access management → add that email as a
   **Viewer**.
7. Set `GSC_SITE_URL`. For a domain property this is
   `sc-domain:leira.no`, not a URL. For a URL-prefix property it's the
   exact prefix including protocol and trailing slash. Mismatches here
   return empty, not an error.
8. Set `GA4_PROPERTY_ID` to the numeric property ID from GA4 Admin →
   Property details. Not the measurement ID (`G-XXXX`) — the number.

Verify before running the full pull:

```bash
python -c "
import config, db
from sources import google
db.init()
with db.connect() as c:
    print('gsc :', google.pull_gsc(c, 'probe'))
    print('ga4 :', google.pull_ga4(c, 'probe'))
"
```

Non-zero on both means you're wired. Zero on GSC usually means step 5 or
7; zero on GA4 usually means step 6 or 8.

Two data caveats worth holding in mind: GSC lags about two days and
drops anonymised queries entirely, so treat it as intent vocabulary
rather than a complete picture. GA4 site-search terms only exist if site
search was configured on the property — the pull skips that block
silently when it isn't.

---

## Usage

```bash
python run.py --account acme doctor          # check creds before spending
python run.py --account acme pull            # fetch all sources
python run.py --account acme estimate        # price it first, free
python run.py --account acme claims          # extract + cluster (costs tokens)
python run.py --account acme score --top 15  # rank, re-runnable free
python run.py --account acme brief --top 5   # write filming briefs
```

Stages are separate on purpose. Pulling is slow and rate-limited, claim
extraction costs money, and you'll want to re-run scoring with different
weights without paying for either again.

`estimate` counts the actual batches that would be sent, prices them at
each model, and spends nothing doing it. Extraction is also idempotent:
every processed signal is marked, including the ones that correctly
yielded no claims, so re-running after a fresh pull only pays for what
arrived since.

`--limit` samples proportionally across sources rather than taking the
first N rows. That matters more than it sounds — source diversity is the
highest-weighted term in the ranking, so a sample drawn from one source
gives every angle an identical diversity score and produces a bank that
looks fine and is ranked on nothing.

First run on yourself:

```bash
python run.py --account self pull
python run.py --account self claims --limit 200   # cap the spend
python run.py --account self score --top 20 --json
```

Two files land in `out/`:

- `<account>-angle-bank.md` — the working copy. Source names stay as raw
  adapter slugs so you can grep them against the `signal` table.
- `<account>-angle-bank.html` — the same content, set in the Caldera
  brand, for handing to a client. Self-contained apart from webfonts.

Both come from the same objects in the same call, so they can't disagree.
Design values are read from `brand/tokens.css` at render time rather than
copied into the renderer; `tests/test_brand.py` fails if the two drift.

---

## Client onboarding

```bash
python onboard.py --account acme     # → http://localhost:5000
```

The four decisions that reduce abandonment, in order of impact:

**1. OAuth, not service accounts.** A service account makes the client
open Search Console, add an email as a user, then open GA4 and do it
again — two consoles, a pasted string, and no feedback if they get it
wrong. It's the highest-abandon step in the flow. OAuth is one button
and a picker. Service accounts still work for running the tool on your
own properties.

**2. Payoff before the second ask.** Meta connects first and the sync
fires immediately, so real counts are on screen before anything else is
requested. The tally is the hero of the page for a reason: the entire
pitch is *you already have this data*, and a number proves it faster
than any paragraph. Every later ask happens with evidence already
visible.

**3. Delegation, which is the one most people miss.** Onboarding usually
dies not because someone is unwilling but because they don't hold the
credentials — the owner says yes, opens the Google step, and finds their
web agency has the access. Every step has a "someone else manages this"
link producing a single-use invite plus a pre-written message. The
delegate connects that one source and lands nowhere else; the owner
doesn't restart.

**4. Only step one is required.** Optional sources are labelled optional
and the pipeline runs without them.

### OAuth app setup

Meta: developers.facebook.com → create app → add Facebook Login →
redirect URI `<ANGLE_BASE_URL>/connect/meta/callback`. The scopes in
`auth.py` need App Review before non-testers can use it, so add your
first clients as test users while that's pending.

Google: console.cloud.google.com → Credentials → OAuth client ID (Web)
→ redirect URI `<ANGLE_BASE_URL>/connect/google/callback`. Enable the
Search Console API, Analytics Data API, and Analytics Admin API. The
consent screen stays in testing mode for up to 100 users, which is
plenty before verification.

Both need `ANGLE_BASE_URL` to be a real https URL in production, since
neither provider allows non-local http redirects.

---



| Source | What it gives | How you connect it |
|---|---|---|
| Meta Page + IG posts | captions, reach, saves, shares | one click |
| **Meta comments** | **the highest-signal Meta text** | comes with the above |
| GSC | search queries — demand vocabulary | service account, no per-property grant |
| GA4 landing pages | conversion proximity weighting | same service account |
| GA4 site search | what people couldn't find | same service account |
| **Gmail** | **customers writing to you at length** | pick one label |
| **Call transcripts** | **objections said out loud** | HappyScribe key, pick a folder |
| Trustpilot | reviews via sanctioned API | paste your domain |
| Email CSV | subject lines with open rates | legacy; file path |

Everything conversational — Gmail, transcripts, reviews, Meta comments —
passes through `pipeline/scrub.py` before it is stored. Email addresses,
phone numbers, greeting names, signature blocks and quoted reply chains
are removed on the way in, so the raw text is never written down.

That last one is not only a privacy measure. A five-message email thread
quotes itself, so the same sentence would be ingested five times — and
recurrence across independent signals is the largest single input to the
ranking. Leaving quoted text in would inflate exactly the number this
whole product turns on.

The pattern worth noticing: the best sources are conversational, not
broadcast. Posts are the brand talking. Comments, calls, searches and
reviews are customers talking. Angles live in the latter.

Deliberately not included: Reddit and third-party forum mining. That's
where Adlicio and Ad Angle Miner already play, and it pulls toward
category research rather than the first-party wedge.

---

## Design decisions that matter

**Cluster by claim, not by post.** A post is a container. A claim is
"I was worried it wouldn't fit." The same claim recurring across a
caption, four comments, a search query and a call transcript is the
signal — not any single item's engagement number.

**Source diversity outweighs volume.** A claim appearing in three
different sources beats one appearing ten times in one comment thread.
Ten mentions in one thread is usually one loud argument.

**Saves and shares are never summed.** A save means "useful to me."
A share means "this says something about me." Different angle types.
Averaging them destroys the distinction.

**Objections get an explicit bonus.** Cold audiences hold the objection
and almost no ad addresses it. Objection angles are disproportionately
good cold creative.

**Boosted posts are excluded from engagement scoring.** Their metrics
blend organic and paid. Text still feeds claim extraction.

**Rates, not counts.** Save rate over reach, so a big post doesn't win
on distribution alone.

---

## Tuning

Scoring weights are arguments to `score_account()`:

```python
score_account(conn, account,
              w_recurrence=1.0, w_diversity=1.2, w_save=0.8,
              w_share=0.8, w_objection=0.9, w_conversion=0.6)
```

Re-scoring is free — no API calls. Tune against ads you've already run.

---

## What this doesn't tell you

Running it on yourself and eyeballing the output against past ads tells
you whether the pipeline works and whether the angles look plausible.
It does not tell you whether organic-derived angles beat control on cold
audiences. That needs the actual test: five organic-derived scripts,
five control scripts, same budget, same cold audience, same period.

Typical A/B primary-metric win rates sit around 12%, so "slightly
better" is noise.
