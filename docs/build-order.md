# Build order

Sequenced so the cheapest thing that could kill the project happens
first.

---

## 0. The cold test — do this before anything else matters

The premise of this product is that angles derived from organic signal
predict paid ad performance on cold audiences. **That is unproven.** The
best available evidence leans mildly against the naive version: a
502-brand dataset from Dash Social found that engagement *degrades* when
warm-audience content is pushed to cold paid reach, and the widely cited
"Nielsen 20% lift" figure traces back to no primary source at all.

So test it. Manually, before trusting any pipeline output.

1. Pick 2–3 accounts with 12+ months of organic history.
2. Derive 5 angles from their organic corpus. Write 5 scripts.
3. Write 5 control scripts the normal way.
4. Run all 10 cold — same budget, same audience, same period.

**Pass condition:** organic-derived angles beat control on cold CPA
materially more often than chance. Typical A/B primary-metric win rates
sit around 12%, so "slightly better" is noise, not signal.

**If it fails, stop.** Three weeks spent, and something real learned
about the existing ad work regardless.

The trap: a working pipeline producing plausible-looking angles feels
like validation and isn't. Everything looks obvious in hindsight when
it came from your own material. If nothing in the top ten *surprises*
you, the pipeline works and the product doesn't.

---

## 1. Get `pull` working against live credentials

The API adapters have never touched a real endpoint. Expect breakage.

- Instagram insights is the likeliest failure — metric names move
  between API versions and the current code degrades silently.
- Add error surfacing: record what was dropped and why, rather than
  swallowing exceptions.
- Meta scopes need App Review before non-testers can connect. Add first
  clients as test users while that's pending.
- Google: if a pull returns zero, it's almost always the property
  identifier, not the credentials. `sc-domain:example.com` for domain
  properties; the numeric GA4 property ID, not `G-XXXX`.

## 2. Run the full pipeline on your own accounts

Use `--limit 200` on `claims` to cap spend while checking output shape.
Read the angle bank properly. Tune scoring weights against ads already
run — re-scoring is free and needs no API calls.

## 3. Transcripts

Highest-signal source and fully first-party. Check the parser actually
splits your export format into speaker turns rather than one blob; if
the format is unusual, budget ten minutes in `_parse_transcript`.

## 4. Onboarding against a real client

Only after the cold test passes. Watch where they stall — the
prediction is the Google step, and the fix is already built (delegation
links), but watch rather than assume.

## 5. Close the loop

Feed actual ad results back against the angle that produced them. Across
enough accounts this becomes the only genuinely defensible asset in the
business: a cross-account map of which claim types convert cold in which
categories.

Do not build this before there is revenue.

---

## Kill criteria

Stop or rethink if any one fires:

1. The cold test fails.
2. Meta ships broad brand-aware creative generation *including briefing
   and direction* before there are paying customers.
3. The only clients who'll buy have thin organic data — meaning the real
   product is category research, which is already commoditised by
   Adlicio and Ad Angle Miner.
4. Three months in, the service hasn't been serviced because attention
   went elsewhere.
