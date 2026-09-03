"""Scoring. Turns clusters into a ranked angle bank.

Design notes that matter:

  * Recurrence is weighted by SOURCE DIVERSITY. A claim appearing in
    comments AND search queries AND a call transcript beats one
    appearing ten times in comments. Independent confirmation is the
    signal; volume in one channel is often one loud thread.

  * Saves and shares are scored SEPARATELY and never summed. A save
    means "useful to me". A share means "this says something about
    me". They map to different angle types and averaging them destroys
    the distinction.

  * Objections get an explicit bonus. Cold audiences hold the
    objection and almost no ad addresses it, so objection-type angles
    are disproportionately good cold creative.

  * Boosted posts are excluded from engagement scoring because their
    metrics blend organic and paid. Their text still feeds claims.
"""

import math

import config


def _norm(values):
    """Rank-free normalization to 0..1 that tolerates outliers."""
    vals = [v for v in values if v is not None]
    if not vals:
        return lambda v: 0.0
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return lambda v: 0.0 if v is None else 0.5
    return lambda v: 0.0 if v is None else (v - lo) / (hi - lo)


def score_account(conn, account,
                  w_recurrence=1.0, w_diversity=1.2, w_save=0.8,
                  w_share=0.8, w_objection=0.9, w_conversion=0.6):
    clusters = conn.execute(
        "SELECT id, canonical, claim_type FROM cluster WHERE account = ?",
        (account,),
    ).fetchall()
    if not clusters:
        return []

    raw = {}
    for cl in clusters:
        rows = conn.execute(
            """
            SELECT s.source, s.kind, s.saves, s.shares, s.reach,
                   s.impressions, s.conversions, s.is_boosted
            FROM claim c JOIN signal s ON s.id = c.signal_id
            WHERE c.cluster_id = ?
            """,
            (cl["id"],),
        ).fetchall()

        n_claims = len(rows)
        sources = {r["source"] for r in rows}
        kinds = {r["kind"] for r in rows}

        organic = [r for r in rows if not r["is_boosted"]]

        # Save/share rates, not raw counts — a big post shouldn't win
        # on reach alone.
        save_rate, share_rate = [], []
        for r in organic:
            denom = r["reach"] or r["impressions"]
            if denom:
                if r["saves"] is not None:
                    save_rate.append(r["saves"] / denom)
                if r["shares"] is not None:
                    share_rate.append(r["shares"] / denom)

        conv = [r["conversions"] for r in rows if r["conversions"]]

        raw[cl["id"]] = {
            "cluster": cl,
            "n_claims": n_claims,
            "n_sources": len(sources),
            "n_kinds": len(kinds),
            "sources": sorted(sources),
            "save": sum(save_rate) / len(save_rate) if save_rate else None,
            "share": sum(share_rate) / len(share_rate) if share_rate else None,
            "conv": sum(conv) if conv else None,
        }

    n_save = _norm([v["save"] for v in raw.values()])
    n_share = _norm([v["share"] for v in raw.values()])
    n_conv = _norm([v["conv"] for v in raw.values()])
    max_sources = max(v["n_sources"] for v in raw.values()) or 1

    out = []
    for cid, v in raw.items():
        # log so a 50-mention claim doesn't bury a 12-mention one
        recurrence = math.log1p(v["n_claims"]) / math.log1p(
            max(x["n_claims"] for x in raw.values())
        )
        diversity = v["n_sources"] / max_sources
        save_w = n_save(v["save"])
        share_w = n_share(v["share"])
        objection = 1.0 if v["cluster"]["claim_type"] == "objection" else 0.0
        conversion = n_conv(v["conv"])

        total = (
            w_recurrence * recurrence
            + w_diversity * diversity
            + w_save * save_w
            + w_share * share_w
            + w_objection * objection
            + w_conversion * conversion
        )

        conn.execute(
            "INSERT OR REPLACE INTO score (cluster_id, recurrence, "
            "source_diversity, save_weight, share_weight, objection_bonus, "
            "conversion_proximity, total) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, recurrence, diversity, save_w, share_w, objection,
             conversion, total),
        )
        out.append({
            "cluster_id": cid,
            "canonical": v["cluster"]["canonical"],
            "claim_type": v["cluster"]["claim_type"],
            "n_claims": v["n_claims"],
            "sources": v["sources"],
            "total": round(total, 3),
            "breakdown": {
                "recurrence": round(recurrence, 3),
                "diversity": round(diversity, 3),
                "save": round(save_w, 3),
                "share": round(share_w, 3),
                "objection": objection,
                "conversion": round(conversion, 3),
            },
        })

    conn.commit()
    out.sort(key=lambda x: x["total"], reverse=True)
    return out


def verbatims(conn, cluster_id, limit=6):
    rows = conn.execute(
        "SELECT c.verbatim, s.source, s.permalink FROM claim c "
        "JOIN signal s ON s.id = c.signal_id "
        "WHERE c.cluster_id = ? AND LENGTH(c.verbatim) > 3 "
        "ORDER BY LENGTH(c.verbatim) DESC LIMIT ?",
        (cluster_id, limit),
    ).fetchall()
    return [{"text": r["verbatim"], "source": r["source"],
             "permalink": r["permalink"]} for r in rows]
