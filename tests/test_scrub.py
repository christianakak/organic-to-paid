"""Verify the scrubber removes what it should and nothing else.

Two failure directions, and the second is the dangerous one. A scrubber
that misses a phone number leaves a privacy problem you can find later. A
scrubber that eats half a customer's sentence corrupts the corpus
silently — the claim extractor sees a truncated objection, clusters it
somewhere wrong, and nothing anywhere reports a problem.

So the pass-through assertions here matter more than the redaction ones.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from pipeline import scrub


def show(label, text):
    print(f"  {label}: {text!r}")


# ------------------------------------------------------------------
# 1. The thing that must not happen: eating real content
# ------------------------------------------------------------------

CLEAN = (
    "Jeg var usikker på om den ville passe i stua vår, vi har ganske "
    "liten plass. Endte opp med å bestille to og angrer ikke."
)
assert scrub.scrub(CLEAN) == CLEAN, \
    f"scrubber altered text containing nothing sensitive:\n{scrub.scrub(CLEAN)}"
print("PASS: text with no personal data comes back byte-identical")

# "Thanks" inside a sentence is content. Only a line that is nothing but
# a sign-off starts a signature block.
INLINE = "Thanks for the quick reply, that answers it. Best product I've bought."
assert scrub.scrub(INLINE) == INLINE, \
    f"a sign-off word mid-sentence was treated as a signature: {scrub.scrub(INLINE)}"
print("PASS: sign-off words inside a sentence are left alone")

# A lowercase run after a greeting is a sentence, not a name.
SENTENCE = "hei jeg lurte på om den finnes i grå"
assert scrub.scrub(SENTENCE) == SENTENCE, \
    f"greeting rule ate sentence words: {scrub.scrub(SENTENCE)}"
print("PASS: a greeting followed by a real sentence is untouched")

# Bare digit runs are order numbers and prices far more often than they
# are phone numbers.
NUMBERS = "Ordre 20260903 kostet 12995 kroner og kom i to esker."
assert scrub.scrub(NUMBERS) == NUMBERS, \
    f"digits were redacted as a phone number: {scrub.scrub(NUMBERS)}"
print("PASS: order numbers and prices are not mistaken for phone numbers")


# ------------------------------------------------------------------
# 2. A real signed Norwegian email with a quoted chain
# ------------------------------------------------------------------

EMAIL = """Hei Christian,

Takk for raskt svar. Jeg var fortsatt litt usikker på om teppet ville
passe under sofaen vår.

Ring meg gjerne på +47 91 23 45 67 eller svar her.

Med vennlig hilsen
Marius Andersen
Innkjøpssjef, Eksempel AS
marius.andersen@eksempel.no
+47 912 34 567
www.eksempel.no

Den man. 3. sep. 2026 kl. 14:32 skrev Kundeservice <post@bty.no>:
> Hei Marius, takk for henvendelsen. Teppet finnes i tre størrelser.
> Med vennlig hilsen
> Kundeservice
"""

out = scrub.scrub(EMAIL)
show("scrubbed", out)

assert "Marius Andersen" not in out, "signature name survived"
assert "marius.andersen@eksempel.no" not in out, "signature address survived"
assert "Innkjøpssjef" not in out, "signature title survived"
assert "Med vennlig hilsen" not in out, "sign-off line survived"
assert "eksempel.no" not in out, "signature URL survived"
assert "91 23 45 67" not in out, "inline phone number survived"
assert "Hei Christian" not in out, "greeting name survived"
assert "Hei," in out, "the greeting itself should be kept, only the name goes"
print("PASS: signature block, greeting name and inline phone all removed")

assert "tre størrelser" not in out, "quoted reply chain survived"
assert "Kundeservice" not in out, "quoted chain header survived"
print("PASS: quoted reply chain removed")

# The whole point of keeping any of it.
assert "usikker" in out and "passe under sofaen" in out, \
    "the customer's actual objection was destroyed"
print("PASS: the objection itself survives intact")


# ------------------------------------------------------------------
# 3. Recurrence inflation — the scoring bug, not the privacy one
# ------------------------------------------------------------------
#
# A thread quotes itself. Ingesting it whole would make one sentence look
# like five independent mentions, and recurrence is the largest single
# input to the ranking.

THREAD = """Det er fortsatt for dyrt for oss.

On Mon, 3 Sep 2026 at 09:00, Someone <a@b.no> wrote:
> Det er fortsatt for dyrt for oss.
> On Fri, 30 Aug 2026 at 16:10, Someone <a@b.no> wrote:
> > Det er fortsatt for dyrt for oss.
> > On Thu, 29 Aug 2026 at 11:00, Someone <a@b.no> wrote:
> > > Det er fortsatt for dyrt for oss.
"""

cleaned = scrub.scrub(THREAD)
count = cleaned.count("for dyrt for oss")
assert count == 1, (
    f"quoted chain would inflate recurrence: the same sentence appears "
    f"{count} times after scrubbing, should be 1"
)
print("PASS: a self-quoting thread yields the sentence once, not four times")


# ------------------------------------------------------------------
# 4. Each rule fails in the right direction on its own
# ------------------------------------------------------------------

assert scrub.EMAIL_TOKEN in scrub.scrub("skriv til ola@eksempel.no takk")
assert scrub.PHONE_TOKEN in scrub.scrub("mobil +47 912 34 567")
assert scrub.PHONE_TOKEN in scrub.scrub("ring 91 23 45 67 i dag")
assert scrub.LINK_TOKEN in scrub.scrub("se https://eksempel.no/produkt/1")
print("PASS: addresses, phones and links are individually redacted")

assert scrub.strip_quotes("a\n> quoted\nb") == "a\nb", \
    "bare quote-marked lines should go even without a chain header"
print("PASS: '>' lines removed independently of a chain header")

# English sign-off, to confirm the list is not Norwegian-only.
EN = "It arrived bent.\n\nBest regards\nJane Doe\nAcme Ltd\n"
en_out = scrub.scrub(EN)
assert "Jane Doe" not in en_out and "Acme Ltd" not in en_out, \
    f"English signature survived: {en_out!r}"
assert "arrived bent" in en_out
print("PASS: English sign-offs handled as well as Norwegian")

print("\nall scrub checks passed")
