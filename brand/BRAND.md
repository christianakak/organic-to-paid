# Caldera

Forge fire on warm limestone. Raw warm plaster as the canvas, every
orange element reading as glowing embers pressed into the surface.

`tokens.css` holds the values and is the only place they live.
`pipeline/render_html.py` reads that file at render time rather than
restating it, so the two cannot disagree.

## Where this applies

**The angle bank only.** That is the artefact a client receives, and it
should look like something worth what it costs.

It does **not** apply to the onboarding server. That page has its own
quiet system — cool paper, ink, teal for connected state, clay for
errors — defined in `templates/base.html`. Its job is to not spook
someone midway through an OAuth flow. A 189px orange headline is the
wrong instrument for that. Two surfaces, two jobs, two systems; don't
unify them.

## The system in one paragraph

Warm limestone canvas flooded with molten orange. Flat and unshadowed —
hierarchy comes from colour contrast and 40px radii, never from
elevation. Ultrabold compressed type at near-architectural scale carries
all the structural weight. One vivid orange is the only aggressive
chromatic accent against monochrome warm greys, with violet reserved for
the hero halftone and sulfur yellow for tags.

## Colour

| Token | Value | Where it is allowed |
|---|---|---|
| Ember | `#fc5000` | Primary actions, featured stat cards, key highlights. The only aggressive accent. |
| Plasma Violet | `#524ae9` | Hero halftone and one standout card. **Never** a control. |
| Sulfur | `#f5f28e` | Tag and category badges. The only yellow in the system. |
| Limestone | `#f7f6f2` | Card surfaces, content blocks. |
| Pumice | `#e2e2df` | Page canvas. Slightly darker than cards, which is what separates figure from ground without shadows. |
| Obsidian | `#070607` | Text, headings, borders. |
| Chalk | `#ffffff` | Text on dark or ember surfaces only. |

Three chromatic tones total. Adding a fourth is not an extension of the
system, it is a different system.

## Type

**Display** — PP Neue Corp Compact, 400 (the Ultrabold cut), 26px to
189px, line-height 0.94–1.20, tracking **+0.02em**.

The positive tracking is not a preference. At 80px and above the heavy
condensed strokes collide without it. Never set this face with negative
letter-spacing.

PP Neue Corp Compact is licensed from Pangram Pangram. Until it is on
hand, the stack falls through to **Anton** — the reference's own first
substitute. Nothing else needs to change when the real face arrives.

**Body** — DM Sans, weight 500 only. Never Regular, which reads as
anaemic beside the display face; never Bold, which competes with it.

**Meta** — system sans at 12px, for dates and micro-labels where size
economy matters more than brand presence.

Headings live between 26px and 189px. Below 26px the ultrabold weight
stops reading as structural and starts reading as shouting.

## Shape

Three radii, and the variety between them is the point:

- **800px** — pills. Buttons, tags, badges, nav containers.
- **40px** — cards, content blocks, rectangular buttons.
- **100px** — inputs.

No rectangular buttons anywhere.

## Elevation

There is none. Not "subtle" — none. No element casts a shadow. Surfaces
separate by colour: Pumice canvas, then Limestone cards, then Ember
features. The flatness is what keeps the heavy type and the bold orange
from tipping into overwrought.

## Signature motifs

1. **The halftone** — orange dots over a violet-to-orange gradient, at
   hero scale, 40px radius. The most recognisable thing in the system.
   Built from layered CSS radial-gradients here, so it needs no image.
2. **The display headline** — ultrabold compressed at architectural
   scale with 0.94 line-height.
3. **The triple radius** — 100 / 40 / 800, which gives consistent
   roundness without monotony.

## Imagery

None. No photography, no 3D renders, no stock. The halftone is brand
artwork, not decoration, and card image areas are solid Ember or violet
halftone blocks. The language is poster design, not photography.
