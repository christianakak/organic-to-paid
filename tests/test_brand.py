"""The derived-artefact check.

The angle bank's appearance is generated from `brand/tokens.css`. Two
copies of a fact diverge; the only question is whether anything notices.
This is the thing that notices.

It fails if the renderer reads a custom property the brand file does not
define — which is the shape a drift actually takes: a token gets renamed
or removed in the brand file, the renderer keeps asking for the old name,
and `var(--gone)` resolves to nothing rather than erroring. The page
still renders. It just quietly stops being on-brand.

Tested in both directions: a real omission must fail, and the current
files must pass.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from pipeline import render_html

defined = set(render_html.tokens_defined())
used = set(render_html.tokens_used())

assert defined, "brand/tokens.css defined no custom properties at all"
assert used, "the renderer referenced no tokens — it has its own values"

missing = sorted(used - defined)
assert not missing, (
    "renderer references tokens that brand/tokens.css does not define: "
    + ", ".join(missing)
)
print(f"PASS: all {len(used)} tokens the renderer uses are defined in brand")

# The other direction. Without this, the check above would also pass if
# `tokens_used` were broken and returned nothing.
FAKE = "a { color: var(--color-ember); border: var(--not-a-real-token); }"
fake_used = set(render_html.tokens_used(FAKE))
assert "--not-a-real-token" in fake_used, "token extraction is not working"
assert fake_used - defined == {"--not-a-real-token"}, \
    "the check failed to isolate the undefined token"
print("PASS: an undefined token is detected, not passed over")

# Unused tokens are fine — the brand file is allowed to define more than
# one page needs — but worth seeing, because a long list usually means
# the renderer has stopped tracking the brand rather than the reverse.
unused = sorted(defined - used)
print(f"note: {len(unused)} brand tokens unused by this renderer")

# Rules from BRAND.md that are cheap to enforce and easy to break by
# accident, since both are natural instincts when a page looks flat.
css = render_html.LAYOUT_CSS
assert "box-shadow" not in css, \
    "Caldera is shadowless — see BRAND.md, Elevation"
assert "letter-spacing: -" not in css, \
    "never negative tracking on the display face — strokes collide"
print("PASS: no shadows, no negative tracking")
