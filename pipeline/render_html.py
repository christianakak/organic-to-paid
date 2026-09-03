"""Render an angle bank as a Caldera-branded HTML page.

This is not a dashboard and not a UI. It is the same document
`brief.render_markdown` produces, set properly, because it is what a
client actually receives.

Design tokens are read from `brand/tokens.css` at render time rather
than restated here, so the page and the brand cannot drift apart. The
layout CSS below references those custom properties and defines none of
its own colours or type sizes; `tests/test_brand.py` fails if it
references a token the brand file does not define.
"""

import datetime
import html
import re
from pathlib import Path

TOKENS_PATH = Path(__file__).parent.parent / "brand" / "tokens.css"

# The `source` column holds adapter names, which are the right thing in
# the database and the wrong thing on a page someone is paying for.
# The markdown output keeps the raw slugs on purpose — it is the working
# copy, and grepping it against the signal table should work.
SOURCE_LABELS = {
    "meta_ig": "Instagram",
    "meta_page": "Facebook",
    "gsc": "Search",
    "ga4": "Site behaviour",
    "transcript": "Sales calls",
    "trustpilot": "Reviews",
    "email": "Email",
}


def _source(name):
    return SOURCE_LABELS.get(name, name.replace("_", " "))


def _sources(names):
    return ", ".join(_source(n) for n in names)

# Layout only. Every colour, size, radius and spacing value here comes
# through a var() from brand/tokens.css — that is the rule that keeps
# this file from quietly becoming a second brand definition.
LAYOUT_CSS = """
* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--surface-canvas);
  color: var(--color-obsidian);
  font-family: var(--font-body);
  font-weight: var(--weight-medium);
  font-size: var(--text-body);
  line-height: var(--leading-body);
  -webkit-font-smoothing: antialiased;
}

.page {
  max-width: var(--page-max-width);
  margin: 0 auto;
  padding: var(--spacing-56) var(--spacing-32) var(--spacing-80);
}

h1, h2, h3 {
  font-family: var(--font-display);
  font-weight: var(--weight-regular);
  letter-spacing: var(--tracking-heading);
  text-transform: uppercase;
  margin: 0;
}

/* --- Hero ------------------------------------------------------ */
/*
 * The halftone is the system's most recognisable motif. Built from
 * layered radial-gradients rather than an image so the file stays
 * self-contained and survives being emailed.
 */
.hero {
  border-radius: var(--radius-cards);
  background:
    radial-gradient(circle at 1.5px 1.5px,
                    rgba(252, 80, 0, 0.85) 1.4px, transparent 1.5px)
      0 0 / 9px 9px,
    radial-gradient(circle at 1px 1px,
                    rgba(252, 80, 0, 0.45) 1px, transparent 1.1px)
      4.5px 4.5px / 9px 9px,
    linear-gradient(115deg,
                    var(--surface-hero) 0%,
                    var(--surface-hero) 34%,
                    var(--color-ember) 96%);
  color: var(--color-chalk);
  padding: var(--spacing-64) var(--card-padding);
  margin-bottom: var(--spacing-24);
  overflow: hidden;
}

.hero h1 {
  font-size: clamp(56px, 12vw, var(--text-display));
  line-height: var(--leading-display);
  margin: var(--spacing-24) 0 var(--spacing-16);
}

.eyebrow {
  font-family: var(--font-body);
  font-weight: var(--weight-medium);
  font-size: var(--text-body-sm);
  line-height: var(--leading-body-sm);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  opacity: 0.78;
}

/* The ISO date breaks mid-token at narrow widths, because the hyphens
 * are wrap opportunities. It should break before the date or not at all. */
.nowrap { white-space: nowrap; }

.hero-note {
  font-family: var(--font-body);
  font-size: var(--text-body);
  max-width: 52ch;
  opacity: 0.92;
}

/* --- Stat row -------------------------------------------------- */
.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--element-gap);
  margin-bottom: var(--section-gap);
}

.stat {
  background: var(--surface-accent);
  color: var(--color-chalk);
  border-radius: var(--radius-cards);
  padding: var(--card-padding);
}
.stat-label {
  font-size: var(--text-body-sm);
  line-height: var(--leading-body-sm);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  opacity: 0.85;
}
.stat-value {
  font-family: var(--font-display);
  font-weight: var(--weight-regular);
  font-size: var(--text-heading-2xl);
  line-height: var(--leading-heading-2xl);
  letter-spacing: var(--tracking-heading);
  margin-top: var(--spacing-8);
}

/* --- Section headings ------------------------------------------ */
.section-head {
  font-size: var(--text-heading-lg);
  line-height: var(--leading-heading-lg);
  margin-bottom: var(--spacing-24);
}

.rule-dotted {
  border: none;
  border-top: 1.5px dotted var(--color-obsidian);
  margin: var(--section-gap) 0 var(--spacing-32);
}

/* --- Ranked table ---------------------------------------------- */
.table-wrap {
  background: var(--surface-card);
  border-radius: var(--radius-cards);
  padding: var(--card-padding);
  overflow-x: auto;
}
table { border-collapse: collapse; width: 100%; min-width: 620px; }
th, td {
  text-align: left;
  padding: var(--spacing-12) var(--spacing-16) var(--spacing-12) 0;
  border-bottom: 1.5px dotted var(--color-obsidian);
  vertical-align: top;
}
th {
  font-size: var(--text-caption);
  line-height: var(--leading-caption);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
td.rank, td.num {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
tr:last-child td { border-bottom: none; }

/* --- Tag badge -------------------------------------------------- */
.tag {
  display: inline-block;
  background: var(--color-sulfur);
  color: var(--color-obsidian);
  border-radius: var(--radius-pills);
  padding: var(--spacing-4) var(--spacing-12);
  font-size: var(--text-caption);
  line-height: var(--leading-caption);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  white-space: nowrap;
}

/* --- Brief cards ------------------------------------------------ */
.brief {
  background: var(--surface-card);
  border-radius: var(--radius-cards);
  padding: var(--card-padding);
  margin-bottom: var(--spacing-24);
}
.brief h3 {
  font-size: var(--text-heading);
  line-height: var(--leading-heading);
  margin: var(--spacing-16) 0 var(--spacing-20);
}
.brief-meta {
  font-family: var(--font-meta);
  font-weight: var(--weight-regular);
  font-size: var(--text-caption);
  line-height: var(--leading-caption);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: var(--spacing-24);
}

.hook {
  background: var(--surface-accent);
  color: var(--color-chalk);
  border-radius: var(--radius-medium);
  padding: var(--spacing-24) var(--spacing-32);
  font-family: var(--font-display);
  font-weight: var(--weight-regular);
  font-size: var(--text-subheading);
  line-height: var(--leading-subheading);
  letter-spacing: var(--tracking-heading);
  margin-bottom: var(--spacing-24);
}

.field { margin-bottom: var(--spacing-20); }
.field-label {
  font-size: var(--text-caption);
  line-height: var(--leading-caption);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  opacity: 0.6;
  margin-bottom: var(--spacing-4);
}
.script { white-space: pre-wrap; }

.grid-2 {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: var(--element-gap);
}

/* --- Evidence --------------------------------------------------- */
/*
 * The verbatims are half of what a client is paying for. They get the
 * violet, which appears nowhere else on the page.
 */
.evidence {
  background: var(--surface-hero);
  color: var(--color-chalk);
  border-radius: var(--radius-medium);
  padding: var(--spacing-24) var(--spacing-32);
  margin-top: var(--spacing-24);
}
.evidence .field-label { opacity: 0.75; }
.quote {
  margin: 0 0 var(--spacing-16);
  padding-left: var(--spacing-16);
  border-left: 1.5px solid var(--color-chalk);
}
.quote:last-child { margin-bottom: 0; }
.quote cite {
  display: block;
  font-family: var(--font-meta);
  font-size: var(--text-caption);
  line-height: var(--leading-caption);
  font-style: normal;
  opacity: 0.75;
  margin-top: var(--spacing-4);
}
.quote a { color: inherit; }

.foot {
  font-family: var(--font-meta);
  font-weight: var(--weight-regular);
  font-size: var(--text-caption);
  line-height: var(--leading-caption);
  margin-top: var(--section-gap);
}

@media print {
  body { background: var(--color-chalk); }
  .brief, .stats { break-inside: avoid; }
}
"""


def _e(value):
    return html.escape(str(value if value is not None else ""))


def render_html(account, angles, briefs):
    """Return a complete, self-contained HTML angle bank."""
    tokens = TOKENS_PATH.read_text(encoding="utf-8")
    today = datetime.date.today().isoformat()

    objections = sum(1 for a in angles if a["claim_type"] == "objection")
    sources = sorted({s for a in angles for s in a["sources"]})
    top = angles[0]["total"] if angles else 0

    out = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Angle bank — {_e(account)}</title>",
        '<link rel="preconnect" href="https://fonts.googleapis.com">',
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
        'family=Anton&family=DM+Sans:wght@500&display=swap">',
        f"<style>\n{tokens}\n{LAYOUT_CSS}\n</style>",
        "</head><body><div class='page'>",

        # --- Hero -------------------------------------------------
        "<header class='hero'>",
        f"<div class='eyebrow'>Angle bank · {_e(account)} · "
        f"<span class='nowrap'>{today}</span></div>",
        "<h1>What your<br>customers<br>already said</h1>",
        "<p class='hero-note'>Every angle below was said to you first — in a "
        "comment, a search, a call, or a review. Nothing here was invented. "
        "These are hypotheses to test cold, not winners to scale.</p>",
        "</header>",

        # --- Stats ------------------------------------------------
        "<section class='stats'>",
        f"<div class='stat'><div class='stat-label'>Angles ranked</div>"
        f"<div class='stat-value'>{len(angles)}</div></div>",
        f"<div class='stat'><div class='stat-label'>Briefed</div>"
        f"<div class='stat-value'>{len(briefs)}</div></div>",
        f"<div class='stat'><div class='stat-label'>Objection angles</div>"
        f"<div class='stat-value'>{objections}</div></div>",
        f"<div class='stat'><div class='stat-label'>Top score</div>"
        f"<div class='stat-value'>{top}</div></div>",
        "</section>",

        # --- Ranked table -----------------------------------------
        "<h2 class='section-head'>Ranked angles</h2>",
        "<div class='table-wrap'><table>",
        "<thead><tr><th>#</th><th>Angle</th><th>Type</th>"
        "<th>Mentions</th><th>Sources</th><th>Score</th></tr></thead><tbody>",
    ]

    for i, a in enumerate(angles, 1):
        out.append(
            f"<tr><td class='rank'>{i}</td>"
            f"<td>{_e(a['canonical'])}</td>"
            f"<td><span class='tag'>{_e(a['claim_type'].replace('_', ' '))}"
            f"</span></td>"
            f"<td class='num'>{a['n_claims']}</td>"
            f"<td>{_e(_sources(a['sources']))}</td>"
            f"<td class='num'>{a['total']}</td></tr>"
        )

    out += ["</tbody></table></div>", "<hr class='rule-dotted'>",
            "<h2 class='section-head'>Filming briefs</h2>"]

    for i, (a, b) in enumerate(zip(angles, briefs), 1):
        out += [
            "<article class='brief'>",
            f"<span class='tag'>{_e(a['claim_type'].replace('_', ' '))}</span>",
            f"<h3>{i}. {_e(a['canonical'])}</h3>",
            f"<div class='brief-meta'>{a['n_claims']} mentions · "
            f"{_e(_sources(a['sources']))} · score {a['total']}</div>",
            f"<div class='hook'>{_e(b.get('hook'))}</div>",
            "<div class='field'><div class='field-label'>Script</div>"
            f"<div class='script'>{_e(b.get('script'))}</div></div>",
            "<div class='grid-2'>",
            "<div class='field'><div class='field-label'>Scene</div>"
            f"<div>{_e(b.get('scene'))}</div></div>",
            "<div class='field'><div class='field-label'>Talent</div>"
            f"<div>{_e(b.get('talent'))}</div></div>",
            "<div class='field'><div class='field-label'>Props</div>"
            f"<div>{_e(b.get('props'))}</div></div>",
            "</div>",
            "<div class='field'><div class='field-label'>Why this angle</div>"
            f"<div>{_e(b.get('rationale'))}</div></div>",
            "<div class='field'><div class='field-label'>What it tests</div>"
            f"<div>{_e(b.get('testing'))}</div></div>",
        ]

        quotes = b.get("quotes", [])
        if quotes:
            out.append("<div class='evidence'>")
            out.append("<div class='field-label'>In their words</div>")
            for q in quotes:
                cite = _e(_source(q.get("source") or ""))
                if q.get("permalink"):
                    cite = (f"<a href='{_e(q['permalink'])}'>{cite}</a>")
                out.append(
                    f"<blockquote class='quote'>{_e(q.get('text'))}"
                    f"<cite>{cite}</cite></blockquote>"
                )
            out.append("</div>")

        out.append("</article>")

    out += [
        "<p class='foot'>Generated by Angle Engine. Angles are derived from "
        "organic signal and are untested on cold paid audiences — that is "
        "what the filming is for.</p>",
        "</div></body></html>",
    ]
    return "\n".join(out)


def tokens_used(css=LAYOUT_CSS):
    """Every custom property this renderer reads. Used by the brand test."""
    return sorted(set(re.findall(r"var\((--[a-z0-9-]+)\)", css)))


def tokens_defined(path=TOKENS_PATH):
    """Every custom property the brand file defines."""
    text = Path(path).read_text(encoding="utf-8")
    # Not anchored to the line start: tokens.css pairs a size and its
    # line-height on one line, and anchoring made the second of each pair
    # invisible. A var() reference has no colon after it, so this still
    # matches declarations only.
    return sorted(set(re.findall(r"(--[a-z0-9-]+)\s*:", text)))
