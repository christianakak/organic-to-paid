# Handoff — Angle Engine

Written 2026-09-03. Not committed to `main`; nothing has been pushed anywhere.

## What this is, in one paragraph

A tool that reads a business's own organic footprint — Facebook and Instagram posts and the
comment threads under them, Google Search Console queries, GA4 behaviour, Gmail, call
transcripts, Trustpilot reviews — extracts the discrete *claims* customers make, clusters
recurring ones into advertising angles, ranks them, and writes filming briefs. It outputs a
shot list, never a finished ad. Owner: Chris (christian@btygruppen.no), Oslo. Norwegian SMB
clients. First target account is BTY itself, slug `bty`.

### Reading order

1. This file.
2. `CLAUDE.md` — the decisions that must not be undone. Several look like obvious
   improvements until you know why they exist, and the reasoning is there rather than here.
3. `CONTRIBUTING.md` — how to add a source, and the one rule about testing guards in the
   direction that fails.
4. `docs/build-order.md` — what to do in what order, and the kill criteria.
5. `docs/market-findings.md` — the competitive landscape and the evidence about the premise.
   Every research claim in this file is sourced there.
6. `brand/BRAND.md` — what "Caldera" means, since this file uses the word as a known noun.

**Use one account slug throughout.** `bty`. Credentials are stored per account, so
connecting as `bty` and pulling as `self` gives an empty pull against an account that was
never connected — which is indistinguishable from a broken adapter.

---

## Where the work is

| | |
|---|---|
| Repo | `/Users/christiank/organic-to-paid` |
| Branch | `feat/bootstrap-angle-engine` — the only branch that exists |
| Head at writing | `3009a82` |
| Remote | `origin` → `https://github.com/christianakak/organic-to-paid` |
| Pushed | **Yes**, 2026-09-03. Remote and local are both at `3009a82`. |
| `main` | **Does not exist**, locally or on the remote. |
| Python | 3.12 via `uv`. System python is 3.9 and will not work. |

Two traps in that table, both of which have already caught someone:

**The remote exists and has been pushed to.** An earlier version of this file said there was
no remote, because whoever wrote it checked for the `gh` CLI (genuinely absent) and inferred
it instead of running `git remote -v`. The repository was completely empty before
2026-09-03 — `git ls-remote` returned nothing — so the push collided with nothing. Do not
offer to create a remote.

**`main` does not exist anywhere, and the feature branch is currently GitHub's default.**
There is no `refs/heads/main` locally or on the remote; `git switch main` finds nothing. But
`.git/config` carries leftover `branch.main.remote` and `branch.main.merge` keys, and the
session-start git snapshot reports `Current branch: main`, so you will be told twice that a
branch exists which no ref backs.

Because `feat/bootstrap-angle-engine` was the first thing pushed, GitHub made it the default
branch. **That is an open decision for Chris** (see the last section), not something to fix
unilaterally — creating `main` from unreviewed work is his call. The standing rule about
never committing to a default branch currently has nothing to attach to.

### Run the tests

```bash
cd /Users/christiank/organic-to-paid
./run-tests.sh
```

Exits non-zero if any suite failed. **Ten suites passed at `3009a82` on 2026-09-03.**

Do not substitute a shell loop. `for t in tests/*.py; do python "$t" || break; done` exits 0
whether or not a test failed, because `break` succeeds — and piping it to `tail`, which
everyone does, reports the pager's status instead. That loop was in this file, in
`CLAUDE.md`, and in `CONTRIBUTING.md`, presented as the way to verify the suite, until
2026-09-03. `run-tests.sh` was written because of it and has been checked in both
directions: it exits 1 with a deliberately broken test and 0 without.

Plain asserts, no framework, deliberately — see `CONTRIBUTING.md` for why that is a decision
rather than a missing migration.

If `.venv` is missing:

```bash
cd /Users/christiank/organic-to-paid
uv venv --python 3.12
uv pip install -r requirements.txt
```

### The CLI

```bash
.venv/bin/python run.py --account bty setup      # once per machine, needs a human
.venv/bin/python run.py --account bty connect    # once per account, opens a browser
.venv/bin/python run.py --account bty doctor     # before every pull
.venv/bin/python run.py --account bty pull
.venv/bin/python run.py --account bty estimate   # prices `claims`, spends nothing
.venv/bin/python run.py --account bty claims --limit 200
.venv/bin/python run.py --account bty score --top 20
.venv/bin/python run.py --account bty brief --top 5
```

Output lands in `out/` — a markdown working copy and a Caldera-branded HTML file for handing
to a client.

---

## THE BLOCKER — read this before planning anything

**No API call has ever been made to Meta, Google, Gmail, HappyScribe, Trustpilot or
Anthropic from this codebase.** Not one. Every adapter was written against documentation, and
every test uses a stub.

This is blocked on **Chris**, and only Chris, because `run.py setup` is interactive and needs
credentials plus a Google Workspace admin action that only he can perform. It cannot be
completed by an agent.

Do not plan work that assumes live data exists. Do not "just try the pull" — it will return
zero rows and you will not be able to tell whether that is a bug or an empty account, which
is the exact failure `doctor` was built to distinguish.

**The right first action for a fresh session is to ask Chris whether he has run `setup`,**
and if not, to work on something that does not need it.

If Chris is not reachable in your session, do not stall — take the unblocked work in
"Suggested next moves" #2 and leave a note of what you needed.

**The Workspace action only Chris can do**, named explicitly so you can tell him what it is:
in Google Admin console → Security → Access and data control → API controls → Domain-wide
delegation → Add new, paste the service account's numeric client ID and the scope string,
and authorise. `run.py setup` prints both values and waits. Nothing else in setup touches
Workspace settings.

---

## What is done and verified vs. done and believed

### Verified — a check ran, exited zero, and I watched it

| Thing | Which check |
|---|---|
| Scoring maths: cross-source objections rank first, saves and shares stay separate, boosted posts excluded | `tests/test_scoring.py` |
| Onboarding page: empty state, tally, delegation invites, single-use enforcement, CSRF mismatch | `tests/test_onboard.py` |
| Connect flows: a bad key is rejected *and not stored*, settings merge, a domain resolves to a Trustpilot business unit | `tests/test_connect.py` |
| Credential shape checks in both directions | `tests/test_doctor.py` |
| PII and quoted-chain scrubbing; clean text comes back byte-identical | `tests/test_scrub.py` |
| Capped runs span sources; junk filtered free; processed signals never return | `tests/test_claims_selection.py` |
| Cost estimate moves with the corpus and spends nothing | `tests/test_estimate.py` |
| Brand tokens and the HTML renderer have not drifted | `tests/test_brand.py` |
| **The whole pipeline, signal in → filming brief out** | `tests/test_pipeline_e2e.py` |

### Believed but NOT verified — assume these are broken

- **Every source adapter against a live endpoint.** `sources/meta.py`, `sources/google.py`,
  `sources/gmail.py`, `sources/happyscribe.py`, `sources/firstparty.py`.
- **Instagram insights specifically.** Meta renames those metric names between API versions.
  `doctor` will name which requested metrics came back missing; expect this to be the first
  thing that breaks.
- **HappyScribe**, `sources/happyscribe.py`. Request shapes were read off
  dev.happyscribe.com on 2026-09-03 and are locked in by `tests/test_happyscribe.py` with
  HTTP stubbed, but nothing has hit the live API. `list_folders` calls `GET /folders`, which
  is **not a documented endpoint** — it degrades to "no folder filter" with a note in
  `pull_log`. Expect that path to be the one that surprises you.
- **The Trustpilot business-unit lookup**, `resolve_business_unit` in
  `sources/firstparty.py`. Tries `/v1/business-units/find?name=` first, falls back to
  `/v1/business-units/search?query=`, because documentation disagrees about which exists and
  trying both costs one request. Never called for real. The fallback prefers an exact
  identifying-name match and labels anything else "closest match — check this is you",
  because storing the wrong business unit would pull a competitor's reviews into the corpus
  with nothing looking wrong.
- **Whether the LLM prompts produce good claims.** The end-to-end test proves the plumbing,
  with a stub that returns what the prompt asks for. It says nothing about whether a real
  model reads a Norwegian comment thread well. The prompt is `EXTRACT_SYSTEM` in
  `pipeline/claims.py`; reviewing it against real Norwegian comment text pasted in by hand is
  unblocked work that needs no credentials.
- **The `estimate` output ratio.** `OUTPUT_RATIO = 0.14` in `pipeline/estimate.py` is the one
  number in that file assumed rather than measured; input counts are real, from
  `messages.count_tokens`.

  **Do not adjust it from general knowledge.** It exists so Chris can decide which model to
  run extraction on, and that decision is explicitly his. Too low and he approves a run that
  costs more than quoted; too high and he declines a cheap one. To correct it properly: run
  `claims` once for real, then compare. `extract_claims` accumulates `msg.usage.input_tokens`
  and `msg.usage.output_tokens` per batch and prints running spend, but does not persist
  them — if you want the real ratio, log those two counters to `pull_log` before the run
  rather than reconstructing it afterwards.

---

## Results worth not re-deriving

- **Google refresh tokens expire after 7 days while an OAuth app is in Testing status**, and
  restricted scopes (Gmail among them) need a third-party CASA security audit before an app
  can leave Testing. This is why self-runs use a service account, not OAuth. Sourced from
  Google's own docs, 2026-09-03.
- **Chris is a Google Workspace admin at BTY**, which is what makes domain-wide delegation
  viable and removes per-property grants entirely.
- **Corpus scale is "a few thousand items"** (Chris's estimate, 2026-09-03). At that size a
  full extraction run is roughly $10–20 on Opus 5. This is why the Batch API was not built.
- **HappyScribe uses a bearer API key**, not OAuth. Base `https://www.happyscribe.com/api/v1`.
- **Trustpilot has a sanctioned API and Glassdoor does not.** Do not scrape Glassdoor: their
  terms forbid it, they litigate, and reviews carry personal data under GDPR.

---

## Decisions made, including ones that reverse earlier instructions

**Read these before following anything in `README.md` or `docs/`.**

1. **For Chris's own accounts, a service account beats OAuth — the reverse of what the
   original design said.** OAuth tokens expire weekly in Testing status. OAuth remains
   correct for clients, who are not on the Workspace. Both paths are live in the code and
   both are right for their case. `CLAUDE.md` decision 7 has the reasoning.

2. **A Caldera-branded HTML render of the deliverable exists** (`pipeline/render_html.py`),
   narrowing the original "no UI" rule to "no UI for *browsing* angle banks". It is a
   document a client receives, not a dashboard. Do not extend it into one.
   **This change is already applied in `CLAUDE.md`** — do not go hunting for a contradiction
   to fix.

3. **Canonical claims and briefs are Norwegian; verbatim quotes are never translated.** The
   same objection in two languages would otherwise cluster as two angles and halve the
   recurrence count the ranking depends on.

4. **Gmail is bounded to one label the user picks — never a whole mailbox, never a search
   query.** The why matters, because "accept a search query, it is more flexible" is an
   obvious-looking improvement: a label is a deliberate decision about which threads are
   customer conversations, and it is explicable to anyone who later asks what was ingested.
   A query is neither, and widens by accident. Everything conversational is also scrubbed
   before storage — see `pipeline/scrub.py`.

5. **Boosted posts are excluded from engagement scoring, but their text still feeds claim
   extraction.** Both halves matter. Their metrics blend organic and paid so the numbers
   lie; their captions are still things the brand said. A rewrite that drops the second half
   silently shrinks the corpus.

6. **`private/business-plan.md` is untracked, not absent.** It sits inside the working tree
   at `/Users/christiank/organic-to-paid/private/`, which `.gitignore` excludes. Do not
   `git add -f` it.

   It contains commercial specifics and a third-party equity situation. **"Leira" is a
   separate company Chris has an undocumented equity arrangement with**, discussed in that
   file's referral-chain section. His instruction on 2026-09-03: read the file for context if
   useful, but do not act on anything relating to Leira and do not build the production
   referral chain it describes. If a task seems to require it, stop and ask him.

7. **Model:** `claude-opus-5` everywhere by default. Chris asked to see real numbers from
   `estimate` before deciding whether to drop extraction to a cheaper tier. **That decision
   is his and is still open.** Do not make it on his behalf, in either direction.

---

## Tried and abandoned — do not re-derive these

- **The Message Batches API** (50% cheaper). Not built. At a few thousand items the saving is
  a few dollars against a polling state machine. Revisit only if a corpus is ~10x bigger.
- **Prompt caching on the extraction system prompt.** Not built, and this one is a trap.
  Caching only engages above a minimum cacheable prefix, which is **model-dependent and
  currently between 512 and 4096 tokens** — `EXTRACT_SYSTEM` in `pipeline/claims.py` is
  roughly 700 and so may fall below it. Below the minimum, adding `cache_control` silently
  does nothing while looking done. Before trying again: count the prompt with
  `messages.count_tokens`, check the current minimum for the model in use, and verify
  `usage.cache_read_input_tokens` is non-zero across repeated calls. If that counter is zero,
  it is not caching whatever the code says.
- **Embedding-based clustering (HDBSCAN), as the business plan specifies.** Not built. The
  existing LLM clustering carries canonical strings forward across batches, which is the
  property that matters, and `tests/test_pipeline_e2e.py` asserts it. Swapping in embeddings
  is a rewrite with no evidence behind it.
- **Writing `.env.example`.** A permission rule in this machine's Claude Code settings
  (`~/.claude/settings.json` or the project's `.claude/settings.local.json` — check both)
  denies writes to any path matching `.env*`. The template therefore ships as `env.example`,
  no leading dot. This is a local tooling constraint, not a design choice. If you rename it
  back, also restore `!.env.example` to `.gitignore` — the negation was removed on
  2026-09-03 because it guarded a file that could not exist, and without it a renamed
  template vanishes from the repo silently.
- **Reading Google credentials via `gcloud`.** Not installed on this machine.

---

## Instruments, and why each exists

- **`run-tests.sh`** — the only correct way to run the suite. Written because the shell loop
  that preceded it in three files exited 0 whether or not a test failed.
- **`run.py … doctor`** — probes every configured source and reads at least one real row.
  Written because Google answers a wrongly-identified property with an *empty result set
  rather than an error*, so a misconfigured pull reports success and stores nothing. It also
  parses the first transcript and checks it split into speaker turns, because a one-blob
  parse still runs the whole pipeline while merging the sales rep's pitch with the customer's
  objection into a single voice.
- **`run.py … estimate`** — prices a `claims` run using `messages.count_tokens` on real
  batches. Written because `claims` is the only stage that spends money and the only way to
  learn what a run cost was to run it. Costs nothing; run it freely.
- **`pipeline/scrub.py`** — anonymises conversational text before storage. Two reasons, and
  the second is not about privacy: quoted email chains would ingest the same sentence once
  per message, and recurrence across independent signals is the largest single input to the
  ranking.
- **`tests/test_pipeline_e2e.py`** — the only thing that has ever run the full pipeline. Its
  fixture encodes the product's thesis as an assertion: an objection recurring across seven
  sources must outrank one appearing in two.
- **`tests/test_brand.py`** — fails if `pipeline/render_html.py` references a design token
  `brand/tokens.css` does not define. Written because `var(--gone)` resolves to nothing
  rather than erroring, so that drift is silent by default.
- **`tests/test_happyscribe.py`** — locks in three request parameters whose absence fails
  silently, `show_speakers` above all.

---

## What I do not trust, including my own work

- **My own judgement on the HTML deliverable's design.** I built it and then assessed it,
  which is worth less than a fresh reader's view.

  To look at it yourself: there is no committed copy — `out/` is gitignored and empty on a
  clone. Run `./run-tests.sh` (or just `.venv/bin/python tests/test_pipeline_e2e.py`) and it
  writes `/tmp/e2e_out/bty-angle-bank.html` and `.md` from synthetic Norwegian data. Open the
  HTML in a browser. Screenshots taken on 2026-09-03 at 1440px and 390px are in
  `.playwright-mcp/` (`bank-top.png`, `bank-mid.png`, `bank-brief.png`, `bank-mobile.png`),
  which is gitignored — so they exist on Chris's machine and nowhere else.

- **The `OUTPUT_RATIO` constant**, as above.
- **Any claim in this file about an adapter working.** None have run against a live endpoint.
- **The scoring weights** (`w_recurrence=1.0, w_diversity=1.2, w_save=0.8, w_share=0.8,
  w_objection=0.9, w_conversion=0.6`, arguments to `score_account` in `pipeline/score.py`).
  They are reasonable-looking guesses, never tuned against real output because there is none.
  Re-scoring is free and makes no API calls, so tune them once real angles exist.

  **Before you touch `w_diversity`, know what it is load-bearing for.** At 1.2 it is the
  highest weight, and two other things depend on it being highest: the cross-source sampler
  (`CLAUDE.md` decision 12) exists to feed it, and `tests/test_pipeline_e2e.py` asserts that
  an objection spanning seven sources outranks one spanning two. Lower it and that test
  fails. If that happens, the test is not brittle — you have changed the product's premise,
  which is that cross-source recurrence predicts a good angle. Decide that deliberately.

- **The premise of the entire product.** See below.

---

## The thing that matters more than any of the above

`docs/build-order.md` §0 is explicit, and `docs/market-findings.md` holds the sourcing.
Restated here so it is not lost:

The product's premise — that angles derived from organic signal predict paid ad performance
on *cold* audiences — **is unproven, and the available evidence leans mildly against the naive
version.** A 502-brand dataset from Dash Social found engagement degrades when warm content is
pushed to cold paid reach. A peer-reviewed result (PMC6682272) found that once reach is
accounted for, paid boosting adds nothing to engagement. The widely cited "Nielsen 20% lift"
figure traces to no primary source; do not put it in any pitch material.

**A working pipeline producing plausible-looking angles is not validation of this.** Everything
looks obvious in hindsight when it came from your own material. The test is five
organic-derived scripts against five control scripts, run cold, same budget, same audience,
same period — in Ads Manager, not in this repo. Typical A/B primary-metric win rates sit
around 12%, so "slightly better" is noise.

If nothing in the top ten angles *surprises* Chris, the pipeline works and the product does
not.

---

## Suggested next moves, in order

1. **Ask Chris whether `setup` has been run.** Everything live is downstream of it. If he is
   not reachable in your session, go straight to 2 rather than stalling.
2. **Unblocked work needing no credentials:** review `EXTRACT_SYSTEM` in `pipeline/claims.py`
   against real Norwegian comment text pasted in by hand; get a fresh pair of eyes on the
   HTML deliverable (generate it per the note above); measure `EXTRACT_SYSTEM`'s token count
   to settle the prompt-caching question one way or the other.
3. **If setup is done:** `doctor`, then `pull` one source at a time, then fix what breaks.
   Expect Instagram insights first.
4. Then `estimate`, show Chris the number, let him pick a model, then `claims --limit 200`.
5. Check the capped sample actually spans more than one source before trusting any ranking.

## Open with Chris

- **Has `setup` been run?** Blocks everything live.
- **Is `github.com/christianakak/organic-to-paid` public or private?** It was empty before
  2026-09-03 and now holds this branch. Nothing sensitive is tracked — that was checked
  against the tracked file list and by grepping every tracked file for key-shaped strings
  before pushing — but `private/business-plan.md` sits untracked inside the working tree and
  names a third party's equity arrangement. One `git add -f` away from being world-readable.
- **How should `main` come to exist?** Right now the feature branch is GitHub's default,
  because it was pushed into an empty repo. Either merge it through a PR in the browser,
  which creates `main` and leaves a review trail, or push it as `main` directly. His call —
  it makes a default branch out of unreviewed work either way.
- **Which Gmail label holds customer threads?** It must exist and have messages in it before
  connecting, or the tool will correctly report the label as empty.
- **Model choice for extraction**, after seeing `estimate` output. His call, still open.
