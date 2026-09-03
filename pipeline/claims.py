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

Return ONLY a JSON array. One object per input item, in order:
[{"i": 0, "claims": [{"text": "...", "verbatim": "...", "claim_type": "objection"}]}]
"""


def extract_claims(conn, account, batch_size=25, limit=None):
    """Extract claims from every signal that doesn't have any yet."""
    import db

    rows = conn.execute(
        "SELECT s.id, s.kind, s.source, s.text, s.raw FROM signal s "
        "LEFT JOIN claim c ON c.signal_id = s.id "
        "WHERE s.account = ? AND c.id IS NULL AND LENGTH(s.text) > 8",
        (account,),
    ).fetchall()

    if limit:
        rows = rows[:limit]

    total = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
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

        msg = client().messages.create(
            model=config.MODEL,
            max_tokens=4000,
            system=EXTRACT_SYSTEM,
            messages=[{
                "role": "user",
                "content": json.dumps(items, ensure_ascii=False),
            }],
        )
        parsed = _json_from(msg.content[0].text) or []

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
        conn.commit()
        print(f"  claims: {min(i + batch_size, len(rows))}/{len(rows)} "
              f"signals -> {total} claims")

    return total


CLUSTER_SYSTEM = """You group customer claims into canonical angles.

You are given a list of EXISTING canonical claims (may be empty) and a \
batch of NEW claims. For each new claim, either:
  - map it to an existing canonical claim, if it expresses substantially \
the same underlying belief, OR
  - create a new canonical claim.

Two claims belong together when a single ad could address both. Differences \
of wording, intensity, or specific product do not separate them. Genuinely \
different beliefs do.

A canonical claim is written as a short, neutral, customer-voice statement. \
Not a slogan. Not a summary of a category.

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
