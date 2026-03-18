#!/usr/bin/env python3
"""
update.py  —  Scrape one or more lottery draws and refresh everything.

Usage:
    python update.py 3908              # update single draw
    python update.py 3908 3909 3910    # update multiple draws
    python update.py --latest          # auto-detect next missing ID and scrape it
"""

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

# Reuse scraper & stats logic
import importlib.util, types

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

BASE = Path(__file__).parent
scraper_mod = _load("scraper", BASE / "scraper.py")
stats_mod   = _load("stats",   BASE / "stats.py")


# ── Helpers ───────────────────────────────────────────────────────────────────

def open_db():
    conn = sqlite3.connect(BASE / "lottery.db")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def next_missing_id(conn):
    """Return the ID after the current maximum in the DB."""
    c = conn.cursor()
    max_id = c.execute("SELECT MAX(id) FROM lotteries").fetchone()[0]
    return (max_id or 0) + 1


def remove_existing(conn, lottery_id):
    c = conn.cursor()
    c.execute("DELETE FROM prize_tiers WHERE lottery_id=?", (lottery_id,))
    c.execute("DELETE FROM lotteries   WHERE id=?",         (lottery_id,))
    c.execute("DELETE FROM skipped_ids WHERE id=?",         (lottery_id,))
    conn.commit()


# ── Core update ───────────────────────────────────────────────────────────────

async def fetch_and_parse(lottery_id: int) -> dict | None:
    """Fetch one draw and return parsed data dict, or None if not found."""
    import aiohttp
    sem = asyncio.Semaphore(1)
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(
        headers=scraper_mod.HEADERS, connector=connector
    ) as session:
        lid, html, err = await scraper_mod.fetch(session, sem, lottery_id)

    if err:
        print(f"  Fetch error for #{lottery_id}: {err}")
        return None
    if html is None:
        print(f"  #{lottery_id}: not found (404)")
        return None

    data = scraper_mod.parse_lottery(html, lottery_id)
    if data is None:
        print(f"  #{lottery_id}: page exists but no lottery data (may be too old / different format)")
        return None
    return data


def update_draw(conn, lottery_id: int) -> bool:
    """Scrape, remove old entry, insert fresh data. Returns True if saved."""
    print(f"Fetching draw #{lottery_id}…")
    data = asyncio.run(fetch_and_parse(lottery_id))

    if data is None:
        return False

    # Validate it matches the current format (1-37 main, 1-7 strong)
    nums   = [data.get(f"num{i}") for i in range(1, 7)]
    strong = data.get("strong_number")

    if any(n is None for n in nums):
        print(f"  #{lottery_id}: missing winning numbers — skipping")
        return False
    if any(n > 37 for n in nums if n):
        print(f"  #{lottery_id}: numbers outside 1-37 (old format) — skipping")
        return False
    if strong and strong > 7:
        print(f"  #{lottery_id}: strong number {strong} outside 1-7 (old format) — skipping")
        return False

    remove_existing(conn, lottery_id)
    scraper_mod.save_lottery(conn, data)
    print(f"  OK Saved #{lottery_id}  date={data.get('draw_date','?')}  "
          f"numbers={nums}  strong={strong}  "
          f"prize_tiers={len(data.get('prize_tiers', []))}")
    return True


def regenerate_stats():
    """Re-run stats.py to refresh the PNG graphs."""
    print("\nRegenerating graphs…")
    stats_mod.main()
    print("Graphs updated.")


def show_summary(conn):
    c = conn.cursor()
    total = c.execute("SELECT COUNT(*) FROM lotteries").fetchone()[0]
    last  = c.execute("SELECT id, draw_date FROM lotteries ORDER BY id DESC LIMIT 1").fetchone()
    print(f"\nDatabase: {total:,} draws  |  Latest: #{last[0]} ({last[1]})")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Update lottery DB and regenerate stats")
    parser.add_argument("ids", nargs="*", type=int,
                        help="Lottery draw ID(s) to scrape and update")
    parser.add_argument("--latest", action="store_true",
                        help="Scrape the next draw after the current max ID")
    parser.add_argument("--no-stats", action="store_true",
                        help="Skip regenerating graphs after update")
    args = parser.parse_args()

    conn = open_db()

    ids_to_update = list(args.ids)

    if args.latest:
        nid = next_missing_id(conn)
        print(f"--latest: next ID is #{nid}")
        ids_to_update.append(nid)

    if not ids_to_update:
        parser.print_help()
        conn.close()
        sys.exit(0)

    updated = []
    for lid in ids_to_update:
        ok = update_draw(conn, lid)
        if ok:
            updated.append(lid)

    if updated and not args.no_stats:
        regenerate_stats()
    elif not updated:
        print("\nNo draws were updated.")

    show_summary(conn)
    conn.close()


if __name__ == "__main__":
    main()
