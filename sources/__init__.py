"""Source adapters.

Every adapter exposes `pull(conn, account) -> int` and normalises whatever
it fetches into rows of the one `signal` table. A failure in one adapter
must not kill a run — `run.py` catches per source and reports.
"""
