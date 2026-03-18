#!/usr/bin/env python3
"""
Lottery Statistics & Graphs
Generates 3 PNG files:
  1. stats_main_numbers.png   — the 6 main drawn numbers
  2. stats_strong_number.png  — the strong (bonus) number
  3. stats_jackpot_draws.png  — draws where someone won 6+strong jackpot
"""

import sqlite3
from datetime import datetime
from collections import defaultdict
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import seaborn as sns

# ── Config ────────────────────────────────────────────────────────────────────
DB_PATH = "lottery.db"

LOTTERY_DAYS = ["Tuesday", "Saturday", "Thursday"]   # main days, in order
DAY_LABELS   = ["Tuesday", "Saturday", "Thursday", "Other"]

PRIZE_BINS   = [0, 5_000_000, 10_000_000, 20_000_000, 40_000_000, float("inf")]
PRIZE_LABELS = ["<5M", "5-10M", "10-20M", "20-40M", "40M"]

PALETTE = "YlOrRd"

# ── Load data ─────────────────────────────────────────────────────────────────

def load_data():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    rows = c.execute("""
        SELECT id, draw_date, num1, num2, num3, num4, num5, num6,
               strong_number, first_prize_lotto
        FROM lotteries
        ORDER BY id
    """).fetchall()

    # jackpot draws: lotteries where 6+strong had ≥1 winner
    jackpot_ids = {
        r[0] for r in c.execute(
            "SELECT lottery_id FROM prize_tiers "
            "WHERE game_type='regular' AND tier LIKE '6 + %' AND winners > 0"
        )
    }
    conn.close()
    return rows, jackpot_ids


def day_bucket(date_str):
    """Return 'Tuesday' / 'Saturday' / 'Thursday' / 'Other'."""
    d = datetime.strptime(date_str, "%d/%m/%Y").strftime("%A")
    return d if d in LOTTERY_DAYS else "Other"


def prize_bucket(prize):
    """Return index into PRIZE_LABELS."""
    if prize is None:
        return len(PRIZE_LABELS) - 1
    for i in range(len(PRIZE_BINS) - 1):
        if PRIZE_BINS[i] <= prize < PRIZE_BINS[i + 1]:
            return i
    return len(PRIZE_LABELS) - 1


# ── Build frequency tables ────────────────────────────────────────────────────

def build_main_tables(rows, filter_ids=None):
    """
    Returns:
      total[num]          = int
      by_day[num][day]    = int
      by_prize[num][pidx] = int
    """
    num_range = range(1, 50)
    total    = defaultdict(int)
    by_day   = {n: defaultdict(int) for n in num_range}
    by_prize = {n: defaultdict(int) for n in num_range}

    for row in rows:
        lid, date, n1, n2, n3, n4, n5, n6, strong, prize = row
        if filter_ids is not None and lid not in filter_ids:
            continue
        db = day_bucket(date)
        pb = prize_bucket(prize)
        for n in (n1, n2, n3, n4, n5, n6):
            if n:
                total[n] += 1
                by_day[n][db] += 1
                by_prize[n][pb] += 1

    return total, by_day, by_prize


def build_strong_tables(rows, filter_ids=None):
    strong_vals = sorted({r[8] for r in rows if r[8] is not None})
    total    = defaultdict(int)
    by_day   = defaultdict(lambda: defaultdict(int))
    by_prize = defaultdict(lambda: defaultdict(int))

    for row in rows:
        lid, date, *_, strong, prize = row
        if filter_ids is not None and lid not in filter_ids:
            continue
        if strong is None:
            continue
        db = day_bucket(date)
        pb = prize_bucket(prize)
        total[strong] += 1
        by_day[strong][db] += 1
        by_prize[strong][pb] += 1

    return total, by_day, by_prize, strong_vals


# ── Plot helpers ──────────────────────────────────────────────────────────────

def plot_total_bar(ax, total, numbers, title, color="#d64045"):
    counts = [total.get(n, 0) for n in numbers]
    bars = ax.bar(numbers, counts, color=color, edgecolor="white", linewidth=0.4)
    avg = np.mean(counts)
    ax.axhline(avg, color="navy", linewidth=1.2, linestyle="--", label=f"Average ({avg:.1f})")
    ax.set_title(title, fontsize=13, fontweight="bold", pad=8)
    ax.set_xlabel("Number")
    ax.set_ylabel("Total appearances")
    ax.legend(fontsize=9)
    ax.set_xticks(numbers[::3])
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    # annotate top-3 and bottom-3
    sorted_counts = sorted(zip(counts, numbers))
    for cnt, n in sorted_counts[:3]:
        ax.annotate(str(cnt), xy=(n, cnt), ha="center", va="top",
                    fontsize=7, color="#555", xytext=(0, -2), textcoords="offset points")
    for cnt, n in sorted_counts[-3:]:
        ax.annotate(str(cnt), xy=(n, cnt), ha="center", va="bottom",
                    fontsize=7, color="navy", xytext=(0, 2), textcoords="offset points")
    ax.grid(axis="y", alpha=0.3)


def plot_day_heatmap(ax, by_day, numbers, title):
    data = np.array([
        [by_day[n].get(d, 0) for d in DAY_LABELS]
        for n in numbers
    ])
    sns.heatmap(
        data, ax=ax,
        xticklabels=DAY_LABELS,
        yticklabels=numbers,
        cmap=PALETTE,
        linewidths=0.3,
        annot=(len(numbers) <= 20),   # annotate only if not too many rows
        fmt="d",
        cbar_kws={"shrink": 0.7},
    )
    ax.set_title(title, fontsize=13, fontweight="bold", pad=8)
    ax.set_xlabel("Day of week")
    ax.set_ylabel("Number")
    ax.tick_params(axis="y", labelsize=7)


def plot_prize_heatmap(ax, by_prize, numbers, title):
    data = np.array([
        [by_prize[n].get(i, 0) for i in range(len(PRIZE_LABELS))]
        for n in numbers
    ])
    sns.heatmap(
        data, ax=ax,
        xticklabels=PRIZE_LABELS,
        yticklabels=numbers,
        cmap=PALETTE,
        linewidths=0.3,
        annot=(len(numbers) <= 20),
        fmt="d",
        cbar_kws={"shrink": 0.7},
    )
    ax.set_title(title, fontsize=13, fontweight="bold", pad=8)
    ax.set_xlabel("Max jackpot (NIS)")
    ax.set_ylabel("Number")
    ax.tick_params(axis="y", labelsize=7)


# ── Main figure builders ──────────────────────────────────────────────────────

def make_main_figure(rows, filter_ids, filename, suptitle):
    numbers = list(range(1, 50))
    total, by_day, by_prize = build_main_tables(rows, filter_ids)

    fig = plt.figure(figsize=(22, 28))
    fig.suptitle(suptitle, fontsize=16, fontweight="bold", y=0.98)

    # Layout: bar chart on top, two heatmaps below
    gs = fig.add_gridspec(3, 1, hspace=0.45)
    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1])
    ax3 = fig.add_subplot(gs[2])

    plot_total_bar(ax1, total, numbers, "Total appearances per number")
    plot_day_heatmap(ax2, by_day, numbers, "Appearances by day of week")
    plot_prize_heatmap(ax3, by_prize, numbers, "Appearances by jackpot prize tier (NIS)")

    plt.savefig(filename, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved: {filename}")


def make_strong_figure(rows, filter_ids, filename, suptitle):
    total, by_day, by_prize, strong_vals = build_strong_tables(rows, filter_ids)
    numbers = sorted(strong_vals)
    if not numbers:
        print(f"No strong number data for {filename}, skipping.")
        return

    fig = plt.figure(figsize=(18, 20))
    fig.suptitle(suptitle, fontsize=16, fontweight="bold", y=0.98)

    gs = fig.add_gridspec(3, 1, hspace=0.45)
    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1])
    ax3 = fig.add_subplot(gs[2])

    plot_total_bar(ax1, total, numbers, "Total appearances per strong number", color="#4a90d9")
    plot_day_heatmap(ax2, by_day, numbers, "Strong number appearances by day of week")
    plot_prize_heatmap(ax3, by_prize, numbers, "Strong number appearances by jackpot prize tier")

    plt.savefig(filename, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved: {filename}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "axes.spines.top": False,
        "axes.spines.right": False,
    })

    print("Loading data...")
    rows, jackpot_ids = load_data()
    print(f"  {len(rows):,} lotteries total, {len(jackpot_ids):,} with jackpot winner")

    # ── 1. Main numbers — all draws ──────────────────────────────────────────
    make_main_figure(
        rows, None,
        "stats_main_numbers.png",
        "Main Numbers (1-49) — All 2,870 Draws",
    )

    # ── 2. Strong number — all draws ─────────────────────────────────────────
    make_strong_figure(
        rows, None,
        "stats_strong_number.png",
        "Strong (Bonus) Number — All 2,870 Draws",
    )

    # ── 3. Main numbers — jackpot-winning draws only ─────────────────────────
    make_main_figure(
        rows, jackpot_ids,
        "stats_jackpot_main_numbers.png",
        f"Main Numbers — Jackpot-Winning Draws Only (6+Strong, {len(jackpot_ids)} draws)",
    )

    # ── 4. Strong number — jackpot-winning draws only ────────────────────────
    make_strong_figure(
        rows, jackpot_ids,
        "stats_jackpot_strong_number.png",
        f"Strong Number — Jackpot-Winning Draws Only ({len(jackpot_ids)} draws)",
    )

    print("\nDone! Files saved to current directory.")


if __name__ == "__main__":
    main()
