#!/usr/bin/env python3
"""Angle Engine CLI.

    python run.py setup   --account acme    # once per machine
    python run.py connect --account acme    # once per account
    python run.py doctor   --account acme
    python run.py pull     --account acme
    python run.py estimate --account acme    # what claims will cost
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


def cmd_setup(args):
    """One-time provider registration. Verifies rather than trusts."""
    import setup_wizard
    sys.exit(setup_wizard.run(args.account))


def cmd_connect(args):
    """Start the onboarding server, open a browser, report what lands.

    Deliberately not a second OAuth implementation. A browser is
    unavoidable — you have to click Allow on Facebook's own page — so
    the only question was where you land afterwards, and onboard.py
    already has the pickers, the delegation links, the running tally and
    the step ordering that puts a real number on screen before the
    second ask.
    """
    import subprocess
    import threading
    import time

    import onboard

    db.init()
    onboard.ACCOUNT = args.account
    url = f"http://127.0.0.1:{args.port}"

    print(f"\n  Connect sources for '{args.account}'")
    print(f"  {url}\n")
    print("  Leave this running. Counts appear here as data lands.")
    print("  Ctrl-C when you're done.\n")

    threading.Thread(
        target=lambda: onboard.app.run(host="127.0.0.1", port=args.port,
                                       debug=False, use_reloader=False),
        daemon=True,
    ).start()
    time.sleep(1.0)
    subprocess.run(["open", url], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Poll the same counts the page shows, and print only what changed.
    # A wall of identical lines would bury the one line that matters.
    seen = {}
    try:
        while True:
            with db.connect() as conn:
                current = {f"{s}:{k}": n
                           for s, k, n in db.counts(conn, args.account)}
            for key, n in sorted(current.items()):
                if seen.get(key) != n:
                    print(f"  {key:<24} {n:>6}")
                    seen[key] = n
            time.sleep(3)
    except KeyboardInterrupt:
        total = sum(seen.values())
        print(f"\n\n  {total} signals for '{args.account}'.")
        print(f"  Next: python run.py --account {args.account} "
              f"claims --limit 200\n")


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
    from sources import meta, google, firstparty, gmail, happyscribe
    db.init()
    with db.connect() as conn:
        n = 0
        for name, mod in (("meta", meta), ("google", google),
                          ("gmail", gmail), ("happyscribe", happyscribe),
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


def cmd_estimate(args):
    """Price a claims run before paying for it. Spends nothing."""
    from pipeline import estimate
    db.init()
    with db.connect() as conn:
        print(estimate.report(conn, args.account, limit=args.limit))


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

    for name, fn in (("setup", cmd_setup), ("connect", cmd_connect),
                     ("doctor", cmd_doctor), ("pull", cmd_pull),
                     ("estimate", cmd_estimate),
                     ("claims", cmd_claims), ("score", cmd_score),
                     ("brief", cmd_brief), ("all", cmd_all)):
        s = sub.add_parser(name)
        s.set_defaults(fn=fn)
        s.add_argument("--port", type=int, default=5000)
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
