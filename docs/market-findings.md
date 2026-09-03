# Market findings

Condensed from a deep research pass. Full detail in the original report;
this is what bears on build decisions.

## Nobody has built the full loop

The market splits into layers that each touch part of it:

- **Creative analytics** (Motion ~$250/mo, Superads ~$150/mo, DatAds,
  Atria) auto-tag creative by hook and angle, but read **paid**
  performance data. Not organic.
- **Ad libraries** (Foreplay, Minea, BigSpy, Adbeat) are competitor swipe
  files.
- **AI generation** (Icon, Arcads, Pencil, AdCreative.ai) works from
  uploaded brand assets and competitor scraping.
- **Voice-of-customer** (Adlicio, Ad Angle Miner, SparkToro, GummySearch)
  mines reviews and third-party discussion into ranked angles — closest
  to this product's front end, but the corpus is third-party, not the
  brand's own organic performance.

The unbuilt slice: a brand's **own** organic post performance and
comment text as predictive input. Real, but narrow.

## Precedents that inform the no-generation stance

- **Icon** — $9.2M (Founders Fund), launched Feb 2025 at $39/mo as "the
  first AI admaker", pivoted within months to a $1,000–3,000/mo
  human-led agency.
- **Pencil** — acquired by Brandtech 2023, pushed upmarket.
- **MagicBrief** — acquired by Canva for $22.5M, standalone product shut
  off 31 July 2025.

Pattern: standalone creative-intelligence tools get absorbed or forced
upmarket. Service margin holds where SaaS doesn't.

## Platform encroachment

Meta announced (2 Oct 2025) brand-aware creative generation inside Ads
Manager that learns from previous ads *and posts*. That is the
horizontal generation product going native and free — which is exactly
why this product sells direction rather than assets.

## The premise is not established

- The commonly cited "Nielsen 20% lift" figure traces to no primary
  source. Do not use it in any pitch material.
- Dash Social, 502 brands: boosted posts showed *lower* comments, saves
  and shares from the boosted effort than the same posts earned
  organically. Engagement degrades when warm content hits cold reach.
- Peer-reviewed (PMC6682272): once reach is accounted for, paid boosting
  adds nothing to engagement.
- Practitioner consensus: engagement optimisation selects for habitual
  engagers, not buyers.

Implication for the product: organic signal generates angle
**hypotheses** that must be re-tested cold. It is not a "boost your
winners" engine, and should never be sold as one.

## Data access constraints

- **Glassdoor** — no public API since 2024, terms forbid scraping, they
  litigate, and reviews carry personal data under GDPR. Do not touch.
- **Trustpilot** — has a sanctioned API. Use it.
- **GSC** — sampled, lags ~2 days, drops anonymised queries. Intent
  vocabulary, not a complete picture.
- **Meta Graph** — organic insights available for owned accounts but
  version-churny; v22 deprecated several IG endpoints.
- **LinkedIn organic** — heavily gated, partner-restricted. Treat as
  unavailable until verified against current developer docs.
