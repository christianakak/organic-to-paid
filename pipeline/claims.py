"""Claim extraction and clustering.

The core design decision: cluster by CLAIM, not by post.

A post is a container. A claim is "this saves me time in the morning"
or "I was worried it wouldn't fit". The same claim recurs across a
caption, four comments, a search query and a call transcript — and that
recurrence across independent sources is the signal, not any single
item's engagement number.

Clustering is done by LLM canonicalization rather than embeddings: we
keep a growing list of canonical claims per account and ask the model
to map each new claim onto an existing one or open a new one. At the
volumes involved (< ~5k claims per account) this is cheaper and more
legible than an embedding + HDBSCAN pass, and the canonical strings are
directly usable in the deliverable.
"""

import json
import os
import re

import anthropic

import config

_client = None


def client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _json_from(text):
    """Models occasionally fence JSON. Strip it."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start = min(
        (i for i in (text.find("["), text.find("{")) if i != -1),
        default=-1,
    )
    if start == -1:
        return None
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        return None


EXTRACT_SYSTEM = """You extract advertising-relevant claims from raw customer signal.

A CLAIM is a single discrete belief, desire, worry, or statement of use \
expressed in the text. Not a summary of the text — a claim inside it.

For each input item return 0-3 claims. Return 0 claims for content that \
carries no customer belief: pure logistics, emoji-only reactions, spam, \
the brand's own promotional copy, tagging, one-word praise.

claim_type is one of:
  benefit   - something good the customer gets or wants
  objection - a worry, doubt, barrier, or reason not to buy
  identity  - something about who they are or want to be seen as
  proof     - evidence, result, or credibility signal
  use_case  - a specific situation the thing is used in

Rules:
- `text` is the claim stated neutrally and generally, so the same claim \
from different people collapses to the same wording where possible.
- `verbatim` is the customer's actual words, trimmed. Never paraphrase \
into verbatim.
- If the speaker is clearly the company/rep rather than a customer, only \
extract claims if they reveal a customer belief being responded to.
- Objections are disproportionately valuable. Do not soften or skip them.

LANGUAGE — this is not optional and not cosmetic:
- `text` is ALWAYS written in Norwegian bokmål, whatever language the \
source was in. The same belief expressed in Norwegian and in English must \
come out as the same Norwegian sentence, or it splits into two angles and \
the recurrence count that drives the whole ranking is halved.
- `verbatim` is NEVER translated. It is the customer's exact words in \
whatever language they used. Translating it destroys the only thing in \
the output that is unarguably real.
- `claim_type` stays one of the five English keys above. It is an \
identifier, not prose.

Return ONLY a JSON array. One object per input item, in order:
[{"i": 0, "claims": [{"text": "...", "verbatim": "...", "claim_type": "objection"}]}]
"""


# Text that cannot contain a claim, filtered locally before anything is
# sent. EXTRACT_SYSTEM already tells the model to return zero claims for
# these, but you pay to ask. On a comment corpus this is a large share of
# the rows and none of the value.
#
# Deliberately timid. "for dyrt" is two words and a complete objection;
# eating it to save a fraction of a cent would be a bad trade, so the
# thresholds sit below anything that could carry a belief.
_EMOJI_OR_PUNCT = re.compile(
    r"^[\s\W\d_]*$",
    re.UNICODE,
)
_TAGS_ONLY = re.compile(r"^(?:[@#][\w.\-]+[\s,]*)+$", re.UNICODE)


def is_extractable(text, kind=None):
    """False for text that provably carries no customer belief.

    `kind` matters for one rule. In a comment, a single word is praise —
    "Kjempebra" is not a claim. In a search query or a page title it is
    the whole signal: someone typing "passform", or a page called
    "Størrelsesguide", is unambiguous evidence of a sizing concern, and
    those are exactly the sources that make an angle cross-source.

    Dropping them would quietly bias the corpus toward comments, which
    are the source most likely to over-represent one loud argument —
    the failure the diversity weighting exists to prevent.
    """
    if not text:
        return False
    text = text.strip()
    if len(text) < 6:
        return False
    if _EMOJI_OR_PUNCT.match(text):        # emoji, punctuation, bare digits
        return False
    if _TAGS_ONLY.match(text):             # "@ola #interiør"
        return False
    if kind in ("query", "page"):
        return True
    if len(text.split()) < 2:              # one word is praise, not a claim
        return False
    return True


def select_signals(conn, account, limit=None):
    """Signals still needing extraction, sampled across sources when capped.

    The cap exists so a first run can check the output shape without
    paying for the whole corpus. Taking the first N rows in table order
    would defeat it: rows arrive in pull order, so a capped run would see
    one source and nothing else.

    That matters more than it sounds. Source diversity is the highest
    weighted term in the ranking — a claim appearing in comments *and*
    search *and* a sales call is the whole thesis — so a single-source
    sample makes every diversity score identical and the resulting bank
    is ranked on nothing while looking entirely plausible.

    So: proportional across sources, newest first within each. An account
    that is 80% comments should get a sample that is mostly comments,
    but every source present gets at least a few rows.
    """
    rows = conn.execute(
        "SELECT id, kind, source, text, raw FROM signal "
        "WHERE account = ? AND claimed_at IS NULL AND LENGTH(text) > 8 "
        "ORDER BY source, COALESCE(created_at, fetched_at) DESC, id DESC",
        (account,),
    ).fetchall()

    rows = [r for r in rows if is_extractable(r["text"], r["kind"])]

    if not limit or len(rows) <= limit:
        return rows

    by_source = {}
    for r in rows:
        by_source.setdefault(r["source"], []).append(r)

    # A floor per source, so nothing is invisible at small caps, then the
    # rest shared out in proportion to how much of the corpus each source
    # actually is.
    sources = sorted(by_source)
    floor = max(1, min(5, limit // max(1, len(sources))))

    picked, taken = [], {}
    for src in sources:
        take = min(floor, len(by_source[src]))
        picked.extend(by_source[src][:take])
        taken[src] = take

    remaining = limit - len(picked)
    pool = sum(len(by_source[s]) - taken[s] for s in sources)

    for src in sources:
        if remaining <= 0 or pool <= 0:
            break
        left = len(by_source[src]) - taken[src]
        share = min(left, round(remaining * left / pool)) if pool else 0
        picked.extend(by_source[src][taken[src]:taken[src] + share])
        taken[src] += share

    # Rounding can leave the sample a row or two short of the cap.
    if len(picked) < limit:
        for src in sources:
            while len(picked) < limit and taken[src] < len(by_source[src]):
                picked.append(by_source[src][taken[src]])
                taken[src] += 1

    return picked[:limit]


def build_batch_payload(batch):
    """The user-message content for one extraction batch.

    Split out so `pipeline/estimate.py` can price the exact payload that
    would be sent rather than an approximation of it.
    """
    items = []
    for j, r in enumerate(batch):
        speaker = ""
        if r["raw"]:
            try:
                speaker = (json.loads(r["raw"]) or {}).get("speaker", "")
            except (json.JSONDecodeError, TypeError):
                pass
        items.append({
            "i": j,
            "source": r["source"],
            "kind": r["kind"],
            "speaker": speaker,
            "text": r["text"][:1500],
        })
    return json.dumps(items, ensure_ascii=False)


def extract_claims(conn, account, batch_size=25, limit=None):
    """Extract claims from every signal not yet processed."""
    import db

    # Rows the pre-filter rejects are considered and dismissed for free.
    # Marking them keeps the "waiting" count honest — otherwise every
    # emoji sits in the estimate forever, making a finished corpus look
    # like there is work left in it.
    skipped = conn.execute(
        "SELECT id, kind, text FROM signal WHERE account = ? "
        "AND claimed_at IS NULL AND LENGTH(text) > 8",
        (account,),
    ).fetchall()
    skipped = [r for r in skipped if not is_extractable(r["text"], r["kind"])]
    if skipped:
        conn.executemany(
            "UPDATE signal SET claimed_at = ? WHERE id = ?",
            [(_now(), r["id"]) for r in skipped],
        )
        conn.commit()
        print(f"  claims: {len(skipped)} signals filtered locally, free")

    rows = select_signals(conn, account, limit)
    total = 0
    spent_in = spent_out = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        try:
            msg = client().messages.create(
                model=config.MODEL,
                max_tokens=4000,
                system=EXTRACT_SYSTEM,
                messages=[{
                    "role": "user",
                    "content": build_batch_payload(batch),
                }],
            )
        except Exception as e:
            # One bad batch must not end a run that has already cost
            # money. These signals stay unmarked, so the next run picks
            # them up rather than losing them.
            db.record(conn, account, "claims", "error",
                      f"batch at offset {i}: {e}")
            conn.commit()
            print(f"  claims: batch at {i} FAILED — {e}")
            continue

        spent_in += msg.usage.input_tokens
        spent_out += msg.usage.output_tokens

        parsed = _json_from(msg.content[0].text)
        if parsed is None:
            # Unparseable is not the same as "no claims here", and
            # marking these processed would silently discard real signal.
            # Leave them for the next run.
            db.record(conn, account, "claims", "dropped",
                      f"batch at offset {i}: response was not JSON")
            conn.commit()
            print(f"  claims: batch at {i} returned unparseable JSON, "
                  f"left for a later run")
            continue

        for entry in parsed:
            idx = entry.get("i")
            if not isinstance(idx, int) or idx >= len(batch):
                continue
            signal_id = batch[idx]["id"]
            for c in entry.get("claims", [])[:3]:
                ctype = c.get("claim_type", "benefit")
                if ctype not in config.CLAIM_TYPES:
                    ctype = "benefit"
                conn.execute(
                    "INSERT INTO claim "
                    "(account, signal_id, text, verbatim, claim_type) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (account, signal_id, c.get("text", "").strip(),
                     (c.get("verbatim") or "").strip(), ctype),
                )
                total += 1

        # The call succeeded and its response parsed, so every signal in
        # this batch has been considered — including the ones that
        # correctly produced nothing. Marking them is what stops the next
        # run paying to reconsider spam.
        conn.executemany(
            "UPDATE signal SET claimed_at = ? WHERE id = ?",
            [(_now(), r["id"]) for r in batch],
        )
        conn.commit()

        done = min(i + batch_size, len(rows))
        print(f"  claims: {done}/{len(rows)} signals -> {total} claims "
              f"({_cost(spent_in, spent_out)} so far)")

    return total


def _now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _cost(tokens_in, tokens_out):
    """Running spend, so a run costing more than the estimate said is
    visible while it is still running rather than afterwards."""
    from pipeline import estimate
    price = estimate.PRICES.get(config.MODEL)
    if not price:
        return f"{tokens_in + tokens_out:,} tokens"
    usd = (tokens_in / 1e6) * price[0] + (tokens_out / 1e6) * price[1]
    return f"${usd:,.2f}"


CLUSTER_SYSTEM = """You group customer claims into canonical angles.

You are given a list of EXISTING canonical claims (may be empty) and a \
batch of NEW claims. For each new claim, either:
  - map it to an existing canonical claim, if it expresses substantially \
the same underlying belief, OR
  - create a new canonical claim.

Two claims belong together when a single ad could address both. Differences \
of wording, intensity, or specific product do not separate them. Genuinely \
different beliefs do.

A canonical claim is written as a short, neutral, customer-voice statement \
in Norwegian bokmål. Not a slogan. Not a summary of a category. Existing \
canonicals are already Norwegian; match their register rather than \
inventing a parallel English vocabulary beside them.

Return ONLY JSON:
[{"i": 0, "canonical": "exact existing string OR a new one", "claim_type": "objection"}]
"""


def cluster_claims(conn, account, batch_size=40):
    """Assign every unclustered claim to a canonical cluster."""
    rows = conn.execute(
        "SELECT id, text, verbatim, claim_type FROM claim "
        "WHERE account = ? AND cluster_id IS NULL",
        (account,),
    ).fetchall()

    canon = {
        r["canonical"]: r["id"]
        for r in conn.execute(
            "SELECT id, canonical FROM cluster WHERE account = ?",
            (account,),
        ).fetchall()
    }

    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        payload = {
            "existing": sorted(canon.keys()),
            "new": [
                {"i": j, "text": r["text"], "claim_type": r["claim_type"]}
                for j, r in enumerate(batch)
            ],
        }
        msg = client().messages.create(
            model=config.MODEL,
            max_tokens=4000,
            system=CLUSTER_SYSTEM,
            messages=[{
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            }],
        )
        parsed = _json_from(msg.content[0].text) or []

        for entry in parsed:
            idx = entry.get("i")
            if not isinstance(idx, int) or idx >= len(batch):
                continue
            canonical = (entry.get("canonical") or "").strip()
            if not canonical:
                continue
            ctype = entry.get("claim_type", batch[idx]["claim_type"])
            if ctype not in config.CLAIM_TYPES:
                ctype = batch[idx]["claim_type"]

            if canonical not in canon:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO cluster "
                    "(account, canonical, claim_type) VALUES (?, ?, ?)",
                    (account, canonical, ctype),
                )
                cid = cur.lastrowid or conn.execute(
                    "SELECT id FROM cluster WHERE account = ? AND canonical = ?",
                    (account, canonical),
                ).fetchone()["id"]
                canon[canonical] = cid

            conn.execute(
                "UPDATE claim SET cluster_id = ? WHERE id = ?",
                (canon[canonical], batch[idx]["id"]),
            )
        conn.commit()
        print(f"  clusters: {min(i + batch_size, len(rows))}/{len(rows)} "
              f"claims -> {len(canon)} canonical")

    return len(canon)
