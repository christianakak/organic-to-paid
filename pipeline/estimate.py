"""Price a claim-extraction run before paying for it.

`claims` is the only stage that costs money and, until this existed, the
only way to find out what a run cost was to run it. That is a bad shape
for a stage you are told to cap with `--limit` precisely because you
don't know.

The numbers here come from the actual batches that would be sent, counted
by the API's own tokeniser via `messages.count_tokens` — not from an
average character count, and not from `tiktoken`, which is a different
model's tokeniser and would be wrong in an unknowable direction.

Counting tokens is free. Running this spends nothing.

Output tokens are the estimated half, since nobody can know what the
model will write. The estimate is anchored on a measured ratio rather
than a guess, and is labelled as an estimate everywhere it appears.
"""

import config

# USD per million tokens, (input, output). From the Anthropic pricing
# table; update alongside it. A model missing from here still runs — the
# estimator just reports tokens instead of dollars, which is honest
# rather than confidently wrong.
PRICES = {
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-fable-5-1": (10.00, 50.00),
}

# Output is bounded by max_tokens=4000 per batch but lands far below it:
# the response is one small JSON object per input signal. Measured
# against the shape of EXTRACT_SYSTEM's contract — up to three short
# claims per item — this ratio of output to input is the working
# assumption, and the one number here that is not measured.
OUTPUT_RATIO = 0.14


def _client():
    import anthropic
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def count_batches(conn, account, batch_size=25, limit=None, sample=6):
    """Token-count the real batches. Counts a sample and scales.

    Counting every batch would mean one API round-trip per batch, which
    is slow and pointless — batches are the same shape by construction,
    so a handful measures the shape and the rest is arithmetic.
    """
    from pipeline.claims import (EXTRACT_SYSTEM, build_batch_payload,
                                 select_signals)

    rows = select_signals(conn, account, limit)
    if not rows:
        return {"signals": 0, "batches": 0, "input_tokens": 0,
                "output_tokens": 0, "sampled": 0}

    batches = [rows[i:i + batch_size]
               for i in range(0, len(rows), batch_size)]

    # Spread the sample across the run rather than taking the first few —
    # sources are ordered, so the opening batches are all one source and
    # would misrepresent the average signal length.
    step = max(1, len(batches) // sample)
    chosen = batches[::step][:sample] or batches[:1]

    client = _client()
    counted = 0
    for batch in chosen:
        counted += client.messages.count_tokens(
            model=config.MODEL,
            system=EXTRACT_SYSTEM,
            messages=[{"role": "user",
                       "content": build_batch_payload(batch)}],
        ).input_tokens

    per_batch = counted / len(chosen)
    total_in = int(per_batch * len(batches))

    return {
        "signals": len(rows),
        "batches": len(batches),
        "input_tokens": total_in,
        "output_tokens": int(total_in * OUTPUT_RATIO),
        "sampled": len(chosen),
    }


def price(tokens_in, tokens_out, model):
    p = PRICES.get(model)
    if not p:
        return None
    return (tokens_in / 1e6) * p[0] + (tokens_out / 1e6) * p[1]


def unfiltered_count(conn, account):
    """How many rows the pre-filter removed, so its effect is visible."""
    from pipeline.claims import is_extractable

    rows = conn.execute(
        "SELECT text FROM signal WHERE account = ? "
        "AND claimed_at IS NULL AND LENGTH(text) > 8",
        (account,),
    ).fetchall()
    return len(rows), sum(1 for r in rows if is_extractable(r["text"]))


def report(conn, account, batch_size=25, limit=None):
    raw, kept = unfiltered_count(conn, account)
    if not raw:
        return ("\n  Nothing to extract. Either no signals have been "
                "pulled, or everything has been processed already.\n")

    est = count_batches(conn, account, batch_size, limit)
    if not est["batches"]:
        return "\n  Nothing left to extract.\n"

    lines = [
        "",
        f"  signals waiting     {raw:>10,}",
        f"  after pre-filter    {kept:>10,}   "
        f"({raw - kept:,} dropped locally, free)",
    ]
    if limit:
        lines.append(f"  capped at           {limit:>10,}   "
                     f"(sampled across sources)")
    lines += [
        f"  in this run         {est['signals']:>10,}",
        f"  batches             {est['batches']:>10,}",
        "",
        f"  Token counts measured on {est['sampled']} real batches. "
        f"Output is an estimate.",
        "",
        f"  {'model':<20}{'input':>12}{'output~':>12}{'cost~':>12}",
        f"  {'-' * 56}",
    ]

    for model in ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"):
        usd = price(est["input_tokens"], est["output_tokens"], model)
        mark = "  <- current" if model == config.MODEL else ""
        lines.append(
            f"  {model:<20}"
            f"{est['input_tokens'] / 1e6:>11.2f}M"
            f"{est['output_tokens'] / 1e6:>11.2f}M"
            f"{'$' + format(usd, ',.2f'):>12}{mark}"
        )

    lines += [
        "",
        "  Clustering runs after this and costs less — claims are much "
        "shorter than",
        "  the signals they came from. Briefs are a handful of calls.",
        "",
        "  Change the model with ANGLE_MODEL in .env. Extraction quality "
        "is the",
        "  floor everything else sits on: a missed objection is never "
        "ranked, never",
        "  briefed, and nothing reports that it went missing.",
        "",
    ]
    return "\n".join(lines)
