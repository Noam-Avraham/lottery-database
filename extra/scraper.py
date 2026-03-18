#!/usr/bin/env python3
"""
Pais Lotto Archive Scraper
Fetches all lottery results from pais.co.il/lotto/archive.aspx
and stores them in a local SQLite database.

Usage:
    python scraper.py              # scrape everything
    python scraper.py --sample 3907  # save raw HTML of one ID to sample.html
    python scraper.py --stats        # print DB summary without scraping
"""

import argparse
import asyncio
import logging
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────
DB_PATH     = Path("lottery.db")
BASE_URL    = "https://www.pais.co.il/lotto/currentlotto.aspx?lotteryId={}"
MIN_ID      = 1
MAX_ID      = 3910          # slightly above latest known (3907)
CONCURRENCY = 8             # simultaneous HTTP requests
BATCH_DELAY = 0.2           # seconds between batches (be polite)
TIMEOUT_SEC = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "he-IL,he;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("scraper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── Database ──────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS lotteries (
    id              INTEGER PRIMARY KEY,   -- lotteryId in URL
    draw_date       TEXT,                  -- DD/MM/YYYY
    draw_time       TEXT,                  -- HH:MM
    num1 INTEGER, num2 INTEGER, num3 INTEGER,
    num4 INTEGER, num5 INTEGER, num6 INTEGER,
    strong_number   INTEGER,
    first_prize_lotto  INTEGER,            -- planned 1st prize (regular)
    first_prize_double INTEGER,            -- planned 1st prize (double)
    total_prizes    INTEGER,               -- total prize pool
    scraped_at      TEXT
);

CREATE TABLE IF NOT EXISTS prize_tiers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lottery_id  INTEGER NOT NULL REFERENCES lotteries(id),
    game_type   TEXT NOT NULL,   -- 'regular' | 'double' | 'extra'
    tier        TEXT NOT NULL,   -- '6+חזק' | '6' | '5+חזק' | etc.
    winners     INTEGER,
    prize       INTEGER          -- prize per winner in NIS
);

CREATE TABLE IF NOT EXISTS skipped_ids (
    id          INTEGER PRIMARY KEY,
    reason      TEXT,            -- '404' | 'parse_error' | 'network_error'
    detail      TEXT,
    attempted_at TEXT
);
"""


def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def get_done_ids(conn: sqlite3.Connection) -> set[int]:
    scraped  = {r[0] for r in conn.execute("SELECT id FROM lotteries")}
    skipped  = {r[0] for r in conn.execute("SELECT id FROM skipped_ids")}
    return scraped | skipped


def save_lottery(conn: sqlite3.Connection, data: dict) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO lotteries
           (id, draw_date, draw_time,
            num1, num2, num3, num4, num5, num6,
            strong_number, first_prize_lotto, first_prize_double,
            total_prizes, scraped_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            data["id"], data.get("draw_date"), data.get("draw_time"),
            data.get("num1"), data.get("num2"), data.get("num3"),
            data.get("num4"), data.get("num5"), data.get("num6"),
            data.get("strong_number"),
            data.get("first_prize_lotto"), data.get("first_prize_double"),
            data.get("total_prizes"), data.get("scraped_at"),
        ),
    )
    for tier in data.get("prize_tiers", []):
        conn.execute(
            """INSERT INTO prize_tiers (lottery_id, game_type, tier, winners, prize)
               VALUES (?,?,?,?,?)""",
            (data["id"], tier["game_type"], tier["tier"],
             tier.get("winners"), tier.get("prize")),
        )
    conn.commit()


def save_skipped(conn: sqlite3.Connection, lid: int, reason: str, detail: str = "") -> None:
    conn.execute(
        """INSERT OR REPLACE INTO skipped_ids (id, reason, detail, attempted_at)
           VALUES (?,?,?,?)""",
        (lid, reason, detail[:500], datetime.now().isoformat()),
    )
    conn.commit()


# ── Parser ────────────────────────────────────────────────────────────────────

def _int(text: str | None) -> int | None:
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", str(text))
    return int(digits) if digits else None


def _is_404(soup: BeautifulSoup) -> bool:
    text = soup.get_text()
    return "הדף לא קיים" in text or "404" in soup.title.string if soup.title else False


def parse_lottery(html: str, lottery_id: int) -> dict | None:
    """Return parsed data dict, or None if the page is a 404 / has no data."""
    soup = BeautifulSoup(html, "lxml")

    if _is_404(soup):
        return None

    full_text = soup.get_text(" ", strip=True)

    # Quick sanity check
    if "הגרלה" not in full_text and str(lottery_id) not in full_text:
        return None

    result: dict = {
        "id": lottery_id,
        "scraped_at": datetime.now().isoformat(),
        "prize_tiers": [],
    }

    # ── Date / time ──────────────────────────────────────────────────────────
    m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", full_text)
    if m:
        result["draw_date"] = m.group(1)

    m = re.search(r"שעה[:\s]*(\d{1,2}:\d{2})", full_text)
    if m:
        result["draw_time"] = m.group(1)

    # ── Winning numbers ───────────────────────────────────────────────────────
    # First <ol> with no id and exactly 6 <li> items in range 1–49
    for ol in soup.find_all("ol"):
        if ol.get("id"):          # skip named lists (regularLottoList etc.)
            continue
        items = [_int(li.get_text()) for li in ol.find_all("li", recursive=False)]
        if len(items) == 6 and all(n and 1 <= n <= 49 for n in items):
            for i, n in enumerate(items, 1):
                result[f"num{i}"] = n
            break

    # ── Strong number ─────────────────────────────────────────────────────────
    # <div class="loto_info_num strong"><div aria-label="המספר החזק N">N</div></div>
    strong_div = soup.find("div", class_="strong_num")
    if strong_div:
        inner = strong_div.find("div", class_="loto_info_num")
        if inner:
            val = inner.find("div")
            if val:
                result["strong_number"] = _int(val.get_text())
    # Fallback: aria-label on any div "המספר החזק N"
    if "strong_number" not in result:
        for d in soup.find_all("div", attrs={"aria-label": True}):
            lbl = d.get("aria-label", "")
            m2 = re.search(r"חזק\s+(\d{1,2})", lbl)
            if m2:
                result["strong_number"] = int(m2.group(1))
                break

    # ── First prize amounts ───────────────────────────────────────────────────
    # Sentence: "פרס הראשון ... X ₪ ובדאבל ... Y ₪"
    MIN_PRIZE = 50_000  # sanity floor — ignore spurious small matches
    m = re.search(
        r"פרס הראשון[^0-9]*([\d,]+)[^\d].*?דאבל[^\d]*([\d,]+)",
        full_text, re.DOTALL,
    )
    if m:
        v1 = _int(m.group(1))
        v2 = _int(m.group(2))
        if v1 and v1 >= MIN_PRIZE:
            result["first_prize_lotto"] = v1
        if v2 and v2 >= MIN_PRIZE:
            result["first_prize_double"] = v2
    else:
        m = re.search(r"פרס ראשון[^\d]*([\d,]+)", full_text)
        if m:
            v = _int(m.group(1))
            if v and v >= MIN_PRIZE:
                result["first_prize_lotto"] = v

    # ── Total prizes ──────────────────────────────────────────────────────────
    for pat in [r"סך הפרסים[^\d]*([\d,]+)", r'סה"כ פרסים[^\d]*([\d,]+)',
                r"סכום כולל[^\d]*([\d,]+)"]:
        m = re.search(pat, full_text)
        if m:
            result["total_prizes"] = _int(m.group(1))
            break

    # ── Prize distribution lists ──────────────────────────────────────────────
    # Structure: <ol id="regularLottoList"> / <ol id="doubleLottoList"> /
    #            unnamed <ol> for Extra (5 items)
    # Each <li class="archive_list_item"> holds 3 archive_list_block divs:
    #   [0] tier label   [1] winners count   [2] prize amount
    OL_GAME_MAP = {
        "regularLottoList": "regular",
        "doubleLottoList":  "double",
    }

    def _text_of_block(block_div):
        """Get the visible text from the innermost div of an archive_list_block."""
        inner = block_div.find("div", attrs={"tabindex": True})
        if inner:
            return inner.get_text(strip=True)
        return block_div.get_text(strip=True)

    def _parse_prize_list(ol_el, game_type):
        for li in ol_el.find_all("li"):
            blocks = li.find_all("div", class_="archive_list_block")
            if len(blocks) < 3:
                continue
            tier    = _text_of_block(blocks[0])
            winners = _int(_text_of_block(blocks[1]))
            prize   = _int(_text_of_block(blocks[2]))
            if tier:
                result["prize_tiers"].append(
                    {"game_type": game_type, "tier": tier,
                     "winners": winners, "prize": prize}
                )

    for ol_id, game in OL_GAME_MAP.items():
        ol_el = soup.find("ol", id=ol_id)
        if ol_el:
            _parse_prize_list(ol_el, game)

    # Extra game: unnamed <ol> with exactly 5 <li> items (not the winning numbers)
    for ol in soup.find_all("ol"):
        if ol.get("id"):
            continue
        lis = ol.find_all("li", recursive=False)
        if len(lis) == 5:
            _parse_prize_list(ol, "extra")
            break

    return result


# ── Async fetcher ─────────────────────────────────────────────────────────────

async def fetch(session: aiohttp.ClientSession,
                sem: asyncio.Semaphore,
                lottery_id: int) -> tuple[int, str | None, str | None]:
    """Returns (lottery_id, html_or_None, error_or_None)."""
    url = BASE_URL.format(lottery_id)
    async with sem:
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_SEC),
                allow_redirects=True,
            ) as resp:
                if resp.status == 404:
                    return lottery_id, None, "404"
                if resp.status != 200:
                    return lottery_id, None, f"HTTP {resp.status}"
                html = await resp.text(errors="replace")
                return lottery_id, html, None
        except asyncio.TimeoutError:
            return lottery_id, None, "timeout"
        except Exception as exc:
            return lottery_id, None, str(exc)


async def run_scraper(ids: list[int], conn: sqlite3.Connection) -> None:
    sem = asyncio.Semaphore(CONCURRENCY)
    connector = aiohttp.TCPConnector(limit=CONCURRENCY, ssl=False)

    async with aiohttp.ClientSession(
        headers=HEADERS, connector=connector
    ) as session:
        tasks = [fetch(session, sem, lid) for lid in ids]
        total = len(tasks)
        done  = 0
        ok    = 0
        skips = 0

        for coro in asyncio.as_completed(tasks):
            lid, html, err = await coro
            done += 1

            if err == "404":
                save_skipped(conn, lid, "404")
                log.debug(f"[{done}/{total}] #{lid}: 404")
                skips += 1

            elif err:
                save_skipped(conn, lid, "network_error", err)
                log.warning(f"[{done}/{total}] #{lid}: {err}")
                skips += 1

            else:
                data = parse_lottery(html, lid)
                if data is None:
                    save_skipped(conn, lid, "404_page")
                    log.debug(f"[{done}/{total}] #{lid}: 404 content (HTTP 200)")
                    skips += 1
                else:
                    save_lottery(conn, data)
                    ok += 1
                    if ok % 50 == 0 or done % 200 == 0:
                        log.info(
                            f"[{done}/{total}] #{lid}  date={data.get('draw_date','?')}  "
                            f"saved={ok}  skipped={skips}"
                        )

            if done % CONCURRENCY == 0:
                await asyncio.sleep(BATCH_DELAY)

        log.info(f"Finished: {ok} saved, {skips} skipped/failed out of {total} IDs")


# ── CLI ───────────────────────────────────────────────────────────────────────

def cmd_stats(conn: sqlite3.Connection) -> None:
    c = conn.cursor()
    lotteries = c.execute("SELECT COUNT(*) FROM lotteries").fetchone()[0]
    tiers     = c.execute("SELECT COUNT(*) FROM prize_tiers").fetchone()[0]
    skipped   = c.execute("SELECT COUNT(*) FROM skipped_ids").fetchone()[0]
    first     = c.execute("SELECT MIN(draw_date) FROM lotteries").fetchone()[0]
    last      = c.execute("SELECT MAX(draw_date) FROM lotteries").fetchone()[0]
    skip_404  = c.execute("SELECT COUNT(*) FROM skipped_ids WHERE reason IN ('404','404_page')").fetchone()[0]

    print(f"\n{'='*45}")
    print(f"  Lotteries in DB : {lotteries:,}")
    print(f"  Prize tier rows : {tiers:,}")
    print(f"  Skipped (no data): {skip_404:,}")
    print(f"  Skipped (errors) : {skipped - skip_404:,}")
    print(f"  Date range      : {first}  to  {last}")
    print(f"{'='*45}\n")


async def cmd_sample(lottery_id: int) -> None:
    sem = asyncio.Semaphore(1)
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(headers=HEADERS, connector=connector) as session:
        lid, html, err = await fetch(session, sem, lottery_id)
    if err:
        print(f"Error fetching #{lottery_id}: {err}")
        return
    out = Path("sample.html")
    out.write_text(html, encoding="utf-8")
    print(f"Saved raw HTML to {out}  ({len(html):,} chars)")
    data = parse_lottery(html, lottery_id)
    if data:
        import pprint
        data_display = {k: v for k, v in data.items() if k != "prize_tiers"}
        pprint.pprint(data_display)
        print(f"prize_tiers ({len(data['prize_tiers'])} rows):")
        for t in data["prize_tiers"]:
            print(f"  {t}")
    else:
        print("parse_lottery returned None — check sample.html")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pais Lotto Scraper")
    parser.add_argument("--sample", type=int, metavar="ID",
                        help="Save raw HTML + parse result for one lottery ID")
    parser.add_argument("--stats",  action="store_true",
                        help="Print database stats and exit")
    parser.add_argument("--min",    type=int, default=MIN_ID,
                        help=f"First lottery ID to try (default {MIN_ID})")
    parser.add_argument("--max",    type=int, default=MAX_ID,
                        help=f"Last lottery ID to try (default {MAX_ID})")
    parser.add_argument("--concurrency", type=int, default=CONCURRENCY,
                        help=f"Parallel requests (default {CONCURRENCY})")
    args = parser.parse_args()

    if args.sample:
        asyncio.run(cmd_sample(args.sample))
        return

    conn = open_db()

    if args.stats:
        cmd_stats(conn)
        conn.close()
        return

    done_ids = get_done_ids(conn)
    todo     = [i for i in range(args.min, args.max + 1) if i not in done_ids]

    log.info(f"IDs to scrape: {len(todo)}  (already done: {len(done_ids)})")
    if not todo:
        log.info("Nothing to do.")
        cmd_stats(conn)
        conn.close()
        return

    # Override concurrency from CLI
    if args.concurrency != CONCURRENCY:
        globals()["CONCURRENCY"] = args.concurrency

    asyncio.run(run_scraper(todo, conn))
    cmd_stats(conn)
    conn.close()


if __name__ == "__main__":
    main()
