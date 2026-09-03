# Contributing

## Setup

```bash
uv venv --python 3.12
uv pip install -r requirements.txt
cp env.example .env
```

Nothing here needs credentials to run the tests. `doctor`, `pull`,
`claims` and `brief` all do.

## Tests

```bash
for t in tests/test_*.py; do python "$t" || break; done
```

Plain asserts, no framework. Each file is runnable on its own and prints
what it proved.

| File | What it holds down |
|---|---|
| `test_scoring.py` | Cross-source objections rank first; save and share stay separate; boosted engagement stays out |
| `test_onboard.py` | Empty state, tally, delegation invites, single-use enforcement, CSRF state mismatch |
| `test_doctor.py` | The credential shape checks, in both directions |
| `test_brand.py` | The renderer and `brand/tokens.css` have not drifted |

## The rule about checks

**Every guard is tested in the direction that fails, not only the
direction that passes.** A check that has never failed is not known to
work — a broken guard and a guard with nothing to catch produce
identical output.

This is not theoretical here. `test_scoring.py` used to print

```
PASS: boosted post excluded from engagement rates
```

with no assertion behind it. Removing the boosted filter entirely left
the output byte-identical and the exit code at zero. The fixture couldn't
have caught it either — the boosted post's numbers were unremarkable
enough to vanish into the normalisation.

Both halves are fixed now: there is a real assertion, and the fixture
gives the boosted post the highest save rate in the account so that
counting it would visibly change the result.

So when you add a check, break the thing it guards and watch it fail
before you commit. If it doesn't fail, you have written a comment.

## Derived things

Anything generated from a source is regenerated from that source, never
edited in place, and something has to fail when the two disagree.

The live example is the brand. `brand/tokens.css` holds the values;
`pipeline/render_html.py` reads that file at render time and defines no
colours or sizes of its own. `tests/test_brand.py` fails if the renderer
references a token the brand file doesn't define — which matters because
`var(--gone)` resolves to nothing rather than erroring, so this
particular drift is silent by default.

If you add a second place a fact lives, add the check that notices.

## Style

- Plain Python. Flask for the onboarding server, nothing else.
- Comments explain *why*, not *what*. If a decision looks wrong until you
  know something, write down the something.
- A failure in one source adapter must not kill a run. Catch per source,
  record what was dropped in `pull_log`, keep going.
- Never swallow an exception silently. `pull_log` exists so that "we got
  zero rows" and "we got zero rows because the credentials are wrong" are
  distinguishable after the fact.

## Before changing behaviour

Read the "Decisions that must not be undone" section of `CLAUDE.md`.
Several things in here look like obvious improvements and are not — the
absence of ad generation most of all. Each one has a reason that isn't
visible from the code.

## Commits

Conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`,
`chore:`. The body is for the reasoning; the subject is for the change.

Never commit `.env`, `service-account.json`, `*.db`, or anything under
`data/`, `out/` or `private/`. All are gitignored; check anyway.
