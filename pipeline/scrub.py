"""Anonymise conversational text before it reaches the database.

Two separate reasons, and the second one is not about privacy at all.

**Privacy.** Gmail, transcripts and reviews carry names, addresses and
phone numbers belonging to people who never agreed to be in an ad
research corpus. Scrubbing at ingest rather than at read time means the
raw text is never stored, so there is nothing to leak later and nothing
to have to go back and delete. That is data minimisation at the point of
collection, which is the defensible GDPR posture rather than the
convenient one.

**Correctness.** A five-message email thread quotes its own earlier
messages, so the same sentence arrives five times. Recurrence across
independent signals is the single largest input to the ranking — a claim
appearing in many places is the whole thesis — so ingesting a quote chain
whole would inflate exactly the number the product turns on. Stripping
quoted text is a scoring fix that happens to also be a privacy one.

The rule throughout is that a false negative beats a false positive.
Leaving a phone number in is bad; eating half a customer's sentence
because it looked like a signature is worse, because it corrupts the
corpus silently. Every pattern here is anchored conservatively, and
`tests/test_scrub.py` asserts that text containing nothing sensitive
comes back byte-identical.

What this does not do: find names in running prose. "I spoke to Marius
about it" survives, because catching that needs entity recognition and a
regex that tried would take out half the Norwegian nouns in the corpus.
Greetings and sign-off blocks are handled because they are positional and
safe; the general case is not.
"""

import re

EMAIL_TOKEN = "[email]"
PHONE_TOKEN = "[phone]"
LINK_TOKEN = "[link]"

# --- Quoted reply chains ------------------------------------------
#
# Everything from the first of these to the end of the message is a
# quotation of something already ingested, or about to be.
QUOTE_MARKERS = [
    # "On Mon, 3 Sep 2026 at 14:32, Someone <x@y.no> wrote:"
    r"^\s*On .{0,120}\bwrote:\s*$",
    # "Den man. 3. sep. 2026 kl. 14:32 skrev Someone <x@y.no>:"
    r"^\s*Den .{0,120}\bskrev\b.{0,120}:\s*$",
    r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$",
    r"^\s*-{2,}\s*Opprinnelig melding\s*-{2,}\s*$",
    r"^\s*_{10,}\s*$",              # Outlook's divider
    r"^\s*From:\s.+$",              # forwarded header block
    r"^\s*Fra:\s.+$",
    r"^\s*Sent from my \w+",
    r"^\s*Sendt fra min \w+",
]
_QUOTE_RE = re.compile("|".join(QUOTE_MARKERS), re.M | re.I)

# --- Sign-offs -----------------------------------------------------
#
# Must be alone on its line, optionally with a trailing comma. "Thanks
# for getting back to me so fast" is content; a line containing only
# "Thanks," is the start of a signature block. That distinction is the
# whole reason this is anchored rather than matched anywhere.
SIGNOFF_WORDS = [
    # Norwegian
    "med vennlig hilsen", "vennlig hilsen", "beste hilsen", "med hilsen",
    "mvh", "vh", "hilsen", "ha en fin dag", "ha en fin helg",
    "takk for hjelpen", "på forhånd takk",
    # English
    "best regards", "kind regards", "warm regards", "regards",
    "best wishes", "all the best", "best", "cheers", "sincerely",
    "yours sincerely", "yours faithfully", "many thanks", "thanks",
    "thank you", "talk soon",
]
_SIGNOFF_RE = re.compile(
    r"^[ \t]*(?:" + "|".join(re.escape(w) for w in SIGNOFF_WORDS)
    + r")[ \t]*[,.!]?[ \t]*$",
    re.M | re.I,
)

# --- Greetings -----------------------------------------------------
#
# Keep the greeting, drop the name after it. The name is the only
# personal part and the greeting itself is harmless filler that helps
# the claim extractor see where a message starts.
GREETING_WORDS = [
    "hei", "hei sann", "heisann", "hallo", "halla", "god morgen",
    "god dag", "god kveld", "hi", "hey", "hello", "dear",
    "good morning", "good afternoon", "good evening",
]
#
# The greeting word is matched case-insensitively, but the name after it
# is not — the capital is the entire signal separating a name from a
# sentence. Hence the scoped `(?i:...)` rather than a flag on the whole
# pattern, which would make `[A-ZÆØÅ]` below match lowercase and take the
# first three words off every message opening with "hei".
_GREETING_RE = re.compile(
    r"^([ \t]*(?i:" + "|".join(re.escape(w) for w in GREETING_WORDS) + r"))"
    # One to three capitalised words: a first name, or a full name.
    # Requiring capitals keeps "hei jeg lurte på" — a real sentence
    # opener — from losing its first three words.
    r"[ \t]+((?:[A-ZÆØÅ][\wÆØÅæøå'’-]*[ \t]*){1,3})"
    r"([,.!:]?[ \t]*)$",
    re.M,
)

# --- Identifiers ---------------------------------------------------
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_URL_RE = re.compile(r"\b(?:https?://|www\.)\S+", re.I)

# Conservative on purpose. An international prefix, or a number written
# in the grouped form Norwegians actually use for phone numbers. A bare
# run of eight digits is left alone — it is as likely to be an order
# number, a price, or a postcode-and-something.
_PHONE_RE = re.compile(
    r"(?:"
    r"\+\d{1,3}[\s.-]?(?:\d[\s.-]?){6,12}\d"      # +47 123 45 678
    r"|\b\d{2}[\s.]\d{2}[\s.]\d{2}[\s.]\d{2}\b"   # 12 34 56 78
    r"|\b\d{3}[\s.]\d{2}[\s.]\d{3}\b"             # 123 45 678
    r")"
)


def _cut_at(text, match):
    return text[: match.start()].rstrip() if match else text


def strip_quotes(text):
    """Drop the quoted chain, and any run of `>` lines."""
    text = _cut_at(text, _QUOTE_RE.search(text))
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith(">")]
    return "\n".join(lines)


def strip_signature(text):
    """Drop the sign-off line and the name/title/company block after it."""
    return _cut_at(text, _SIGNOFF_RE.search(text))


def strip_greeting_names(text):
    """`Hei Christian,` -> `Hei,`. Keeps the greeting, loses the person."""
    return _GREETING_RE.sub(lambda m: m.group(1) + m.group(3).rstrip(), text)


def redact_identifiers(text):
    text = _EMAIL_RE.sub(EMAIL_TOKEN, text)
    text = _URL_RE.sub(LINK_TOKEN, text)
    text = _PHONE_RE.sub(PHONE_TOKEN, text)
    return text


def scrub(text):
    """Full pass. Order matters.

    Quotes first, because a quoted chain contains its own signatures and
    greetings and there is no point processing text that is about to be
    thrown away. Redaction last, so the earlier positional rules still
    see the real shapes rather than placeholder tokens.
    """
    if not text:
        return ""

    text = strip_quotes(text)
    text = strip_signature(text)
    text = strip_greeting_names(text)
    text = redact_identifiers(text)

    # Collapse the blank-line runs that cutting tends to leave behind,
    # without touching single paragraph breaks.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
