#!/usr/bin/env python3
"""Angle Engine CLI.

    python run.py doctor  --account acme
    python run.py pull    --account acme
    python run.py claims  --account acme
    python run.py score   --account acme
    python run.py brief   --account acme --top 5
    python run.py all     --account acme --top 5

Stages are separate on purpose: pulling is slow and rate-limited,
claim extraction costs tokens, and scoring you'll want to re-run with
different weights without paying for either again.
"""

import argparse
import json
import sys

import config
import db


def cmd_doctor(args):
    """Probe every configured source before spending a pull on it.

    Google answers a wrongly identified property with an empty result
    set rather than an error, so a misconfigured pull looks like a
    successful one. Run this first.
    """
    import doctor
    db.init()
    with db.connect() as conn:
        rep = doctor.run(conn, args.account)
    print()
    print(rep.render())
    print()
    if rep.failures:
        print(f"{rep.failures} check(s) failed — fix these before pulling.\n")
        sys.exit(1)
    print("Every configured source answered.\n")


def cmd_pull(args):
    from sources import meta, google, firstparty
    db.init()
    with db.connect() as conn:
        n = 0
        for name, mod in (("meta", meta), ("google", google),
                          ("first-party", firstparty)):
            try:
                got = mod.pull(conn, args.account)
                print(f"  {name}: {got} signals")
                n += got
            except Exception as e:
                print(f"  {name}: FAILED — {e}", file=sys.stderr)
        conn.commit()
        print(f"\n{n} new signals\n")
        for source, kind, count in db.counts(conn, args.account):
            print(f"  {source:<12} {kind:<8} {count:>6}")


def cmd_claims(args):
    from pipeline import claims
    with db.connect() as conn:
        n = claims.extract_claims(conn, args.account, limit=args.limit)
        print(f"\n{n} claims extracted")
        k = claims.cluster_claims(conn, args.account)
        print(f"{k} canonical angles\n")


def cmd_score(args):
    from pipeline import score
    with db.connect() as conn:
        angles = score.score_account(conn, args.account)
        for i, a in enumerate(angles[:args.top], 1):
            print(f"{i:>2}. [{a['total']:.2f}] ({a['claim_type']}) "
                  f"{a['canonical']}")
            print(f"     {a['n_claims']} mentions across "
                  f"{', '.join(a['sources'])}")
        if args.json:
            config.OUT_DIR.mkdir(parents=True, exist_ok=True)
            path = config.OUT_DIR / f"{args.account}-angles.json"
            path.write_text(json.dumps(angles, indent=2, ensure_ascii=False))
            print(f"\nwrote {path}")


def cmd_brief(args):
    from pipeline import score, brief, render_html
    with db.connect() as conn:
        angles = score.score_account(conn, args.account)[:args.top]
        briefs = []
        for i, a in enumerate(angles, 1):
            print(f"  briefing {i}/{len(angles)}: {a['canonical'][:60]}")
            briefs.append(brief.write_brief(conn, args.account, a,
                                            args.context or ""))

        config.OUT_DIR.mkdir(parents=True, exist_ok=True)

        # Markdown to read and edit; HTML to hand over. Same content,
        # generated from the same objects, so they cannot disagree.
        md_path = config.OUT_DIR / f"{args.account}-angle-bank.md"
        md_path.write_text(
            brief.render_markdown(args.account, angles, briefs),
            encoding="utf-8",
        )
        html_path = config.OUT_DIR / f"{args.account}-angle-bank.html"
        html_path.write_text(
            render_html.render_html(args.account, angles, briefs),
            encoding="utf-8",
        )
        print(f"\nwrote {md_path}")
        print(f"wrote {html_path}")


def cmd_all(args):
    cmd_pull(args)
    cmd_claims(args)
    cmd_brief(args)


def main():
    p = argparse.ArgumentParser(prog="angle-engine")
    p.add_argument("--account", required=True, help="client slug")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name, fn in (("doctor", cmd_doctor), ("pull", cmd_pull),
                     ("claims", cmd_claims), ("score", cmd_score),
                     ("brief", cmd_brief), ("all", cmd_all)):
        s = sub.add_parser(name)
        s.set_defaults(fn=fn)
        s.add_argument("--top", type=int, default=10)
        s.add_argument("--limit", type=int, default=None,
                       help="cap signals processed (for cheap test runs)")
        s.add_argument("--json", action="store_true")
        s.add_argument("--context", default="",
                       help="one-line brand context for briefs")

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
