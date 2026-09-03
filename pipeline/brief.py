"""Brief generation. Angle -> script + shot direction.

This is the layer being sold, so it is deliberately LLM-assisted and
human-finished rather than automated end to end. The model drafts; you
edit. Output is a filming brief, not an asset.
"""

import datetime
import json

import config
from pipeline.claims import client, _json_from
from pipeline.score import verbatims

BRIEF_SYSTEM = """You write filming briefs for direct-response video ads.

You are given ONE angle: a canonical customer claim, its type, and \
verbatim quotes from real customers that produced it.

Write a brief that tells someone what to film. You are not writing an \
ad; you are directing one.

Constraints:
- The hook must be sayable in under 3 seconds and must NOT be a slogan.
- The script is 15-30 seconds spoken, plain language, the customer's \
vocabulary rather than the brand's.
- Assume a phone camera and one person. No studio, no crew, no motion \
graphics. If the angle needs production it isn't a good first test.
- Scene, talent, and props must be concrete enough to execute without \
asking a follow-up question.
- For an objection angle, name the objection out loud in the first \
line. That is the whole point of the format.
- `rationale` cites the evidence: which quotes and sources drove this.
- `testing` states what belief this ad is trying to change, so the \
result is interpretable.

LANGUAGE: write everything in Norwegian bokmål. Someone is going to \
stand in a room and say the hook and the script out loud to a phone \
camera, and they are Norwegian. Quotes you cite inside `rationale` stay \
in the words the customer actually used.

Return ONLY JSON:
{"hook": "...", "script": "...", "scene": "...", "talent": "...",
 "props": "...", "rationale": "...", "testing": "..."}
"""


def write_brief(conn, account, angle, brand_context=""):
    quotes = verbatims(conn, angle["cluster_id"])
    payload = {
        "angle": angle["canonical"],
        "claim_type": angle["claim_type"],
        "appears_in_sources": angle["sources"],
        "times_mentioned": angle["n_claims"],
        "customer_quotes": [q["text"] for q in quotes],
        "brand_context": brand_context,
    }

    msg = client().messages.create(
        # Not config.MODEL. Extraction runs over the whole corpus and is
        # where spend is worth tuning; this runs over five angles and is
        # the layer being sold. They should be settable separately.
        model=config.BRIEF_MODEL,
        max_tokens=2000,
        system=BRIEF_SYSTEM,
        messages=[{"role": "user",
                   "content": json.dumps(payload, ensure_ascii=False)}],
    )
    data = _json_from(msg.content[0].text) or {}

    conn.execute(
        "INSERT INTO brief (cluster_id, hook, script, scene, talent, "
        "props, rationale, testing, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (angle["cluster_id"], data.get("hook"), data.get("script"),
         data.get("scene"), data.get("talent"), data.get("props"),
         data.get("rationale"), data.get("testing"),
         datetime.datetime.now().isoformat()),
    )
    conn.commit()
    data["quotes"] = quotes
    return data


def render_markdown(account, angles, briefs):
    lines = [
        f"# Angle bank — {account}",
        "",
        f"*Generated {datetime.date.today().isoformat()}. "
        f"{len(angles)} angles ranked; top {len(briefs)} briefed.*",
        "",
        "## Ranked angles",
        "",
        "| # | Angle | Type | Mentions | Sources | Score |",
        "|---|---|---|---|---|---|",
    ]
    for i, a in enumerate(angles, 1):
        lines.append(
            f"| {i} | {a['canonical']} | {a['claim_type']} | "
            f"{a['n_claims']} | {', '.join(a['sources'])} | {a['total']} |"
        )

    lines += ["", "---", "", "## Filming briefs", ""]
    for i, (a, b) in enumerate(zip(angles, briefs), 1):
        lines += [
            f"### {i}. {a['canonical']}",
            "",
            f"**Type:** {a['claim_type']}  ·  **Mentions:** {a['n_claims']}  "
            f"·  **Sources:** {', '.join(a['sources'])}  "
            f"·  **Score:** {a['total']}",
            "",
            f"**Hook**  \n{b.get('hook', '')}",
            "",
            "**Script**",
            "",
            b.get("script", ""),
            "",
            f"**Scene** — {b.get('scene', '')}",
            "",
            f"**Talent** — {b.get('talent', '')}",
            "",
            f"**Props** — {b.get('props', '')}",
            "",
            f"**Why this angle** — {b.get('rationale', '')}",
            "",
            f"**What it tests** — {b.get('testing', '')}",
            "",
            "**Evidence**",
            "",
        ]
        for q in b.get("quotes", []):
            lines.append(f"> {q['text']}  \n> — *{q['source']}*")
            lines.append("")
        lines += ["---", ""]

    return "\n".join(lines)
