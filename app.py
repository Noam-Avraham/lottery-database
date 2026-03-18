from flask import Flask, jsonify, request, render_template, send_from_directory
import sqlite3
import random
from pathlib import Path
from collections import Counter
from itertools import combinations
from math import comb

app = Flask(__name__)
DB = Path(__file__).parent / "lottery.db"


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_draw(draw):
    return {
        "id":      draw["id"],
        "date":    draw["draw_date"],
        "numbers": [draw["num1"], draw["num2"], draw["num3"],
                    draw["num4"], draw["num5"], draw["num6"]],
        "strong":  draw["strong_number"],
        "jackpot": draw["first_prize_lotto"],
    }


def determine_tier(matches, strong_match):
    if   matches == 6 and strong_match: return "6 + חזק"
    elif matches == 6:                  return "6"
    elif matches == 5 and strong_match: return "5 + חזק"
    elif matches == 5:                  return "5"
    elif matches == 4 and strong_match: return "4 + חזק"
    elif matches == 4:                  return "4"
    elif matches == 3 and strong_match: return "3 + חזק"
    elif matches == 3:                  return "3"
    return None


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/search")
def search():
    return render_template("search.html")

@app.route("/facts")
def facts():
    return render_template("facts.html")

@app.route("/lucky")
def lucky():
    return render_template("lucky.html")

@app.route("/images/<filename>")
def serve_image(filename):
    return send_from_directory(Path(__file__).parent, filename)


# ── API ────────────────────────────────────────────────────────────────────────

@app.route("/api/date")
def search_by_date():
    day   = request.args.get("day",   type=int)
    month = request.args.get("month", type=int)

    if not day or not month or not (1 <= day <= 31) or not (1 <= month <= 12):
        return jsonify({"error": "Valid day (1-31) and month (1-12) required"}), 400

    pattern = f"{day:02d}/{month:02d}/%"
    conn = get_db()
    c    = conn.cursor()

    draws = c.execute("""
        SELECT id, draw_date, num1,num2,num3,num4,num5,num6,
               strong_number, first_prize_lotto
        FROM lotteries WHERE draw_date LIKE ? ORDER BY id
    """, (pattern,)).fetchall()

    result = [row_to_draw(d) for d in draws]
    freq   = Counter(n for r in result for n in r["numbers"])
    conn.close()
    return jsonify({
        "draws":            result,
        "total":            len(result),
        "number_frequency": dict(sorted(freq.items(), key=lambda x: -x[1])),
    })


@app.route("/api/combination")
def search_combination():
    nums_str   = request.args.get("nums",   "")
    strong_str = request.args.get("strong", "").strip()

    try:
        nums   = [int(n) for n in nums_str.split(",") if n.strip()]
        strong = int(strong_str) if strong_str else None
    except ValueError:
        return jsonify({"error": "Invalid numbers"}), 400

    if not nums:
        return jsonify({"error": "Enter at least one number (1-37)"}), 400
    if any(n < 1 or n > 37 for n in nums):
        return jsonify({"error": "Main numbers must be between 1 and 37"}), 400
    if strong is not None and (strong < 1 or strong > 7):
        return jsonify({"error": "Strong number must be between 1 and 7"}), 400
    if len(nums) > 6:
        return jsonify({"error": "Maximum 6 main numbers"}), 400

    conn = get_db()
    c    = conn.cursor()

    conds  = ["(num1=? OR num2=? OR num3=? OR num4=? OR num5=? OR num6=?)" for _ in nums]
    params = [v for n in nums for v in [n]*6]

    rows = c.execute(f"""
        SELECT id, draw_date, num1,num2,num3,num4,num5,num6,
               strong_number, first_prize_lotto
        FROM lotteries WHERE {' AND '.join(conds)} ORDER BY id
    """, params).fetchall()

    result = []
    for draw in rows:
        draw_set     = {draw["num1"],draw["num2"],draw["num3"],
                        draw["num4"],draw["num5"],draw["num6"]}
        matches      = len(set(nums) & draw_set)
        strong_match = strong is not None and strong == draw["strong_number"]
        tier         = determine_tier(matches, strong_match)
        prize        = None
        if tier:
            pr = c.execute(
                "SELECT prize FROM prize_tiers "
                "WHERE lottery_id=? AND game_type='regular' AND tier=?",
                (draw["id"], tier)
            ).fetchone()
            prize = pr["prize"] if pr else None
        result.append({**row_to_draw(draw),
                        "matches": matches, "strong_match": strong_match,
                        "tier": tier, "prize": prize})

    conn.close()
    return jsonify({
        "draws":        result,
        "total":        len(result),
        "tier_summary": dict(Counter(r["tier"] for r in result if r["tier"])),
    })


@app.route("/api/facts")
def get_facts():
    conn = get_db()
    c    = conn.cursor()

    draws = c.execute("""
        SELECT id, draw_date, num1,num2,num3,num4,num5,num6,
               strong_number, first_prize_lotto
        FROM lotteries ORDER BY id
    """).fetchall()

    total = len(draws)

    # ── Combinations coverage ──────────────────────────────────────────────
    total_possible = comb(37, 6) * 7   # 16,273,488
    repeated = c.execute("""
        SELECT COUNT(*) FROM (
          SELECT num1,num2,num3,num4,num5,num6,strong_number
          FROM lotteries
          GROUP BY num1,num2,num3,num4,num5,num6,strong_number
          HAVING COUNT(*) > 1
        )
    """).fetchone()[0]

    # ── Most common pairs & triplets ───────────────────────────────────────
    pairs    = Counter()
    triplets = Counter()
    for d in draws:
        nums = [d["num1"],d["num2"],d["num3"],d["num4"],d["num5"],d["num6"]]
        for p in combinations(nums, 2): pairs[p]    += 1
        for t in combinations(nums, 3): triplets[t] += 1

    top_pairs    = [{"nums": list(p), "count": n} for p, n in pairs.most_common(10)]
    top_triplets = [{"nums": list(t), "count": n} for t, n in triplets.most_common(5)]

    # ── Jackpot stats ──────────────────────────────────────────────────────
    jackpot_rows = c.execute("""
        SELECT l.id, l.draw_date, p.winners, p.prize
        FROM lotteries l JOIN prize_tiers p ON l.id = p.lottery_id
        WHERE p.game_type='regular' AND p.tier LIKE '6 + %' AND p.winners > 0
        ORDER BY p.prize DESC
    """).fetchall()

    jackpot_ids = {r["id"] for r in jackpot_rows}

    # Biggest jackpot prize
    biggest = max(jackpot_rows, key=lambda r: r["prize"])
    # Most winners in single draw
    most_w  = max(jackpot_rows, key=lambda r: r["winners"])

    # Longest drought
    streak, best = 0, 0
    for d in draws:
        if d["id"] not in jackpot_ids:
            streak += 1; best = max(best, streak)
        else:
            streak = 0

    # Most common jackpot size
    prize_cnt   = Counter(d["first_prize_lotto"] for d in draws)
    common_p, common_c = prize_cnt.most_common(1)[0]

    # ── Hot / cold (last 50 draws) ─────────────────────────────────────────
    last50   = draws[-50:]
    hot_cnt  = Counter(n for d in last50
                       for n in [d["num1"],d["num2"],d["num3"],d["num4"],d["num5"],d["num6"]])
    # Ensure all 1-37 in cold ranking
    for n in range(1, 38):
        hot_cnt.setdefault(n, 0)

    hot  = [{"num": n, "count": c} for n, c in hot_cnt.most_common(5)]
    cold = [{"num": n, "count": c} for n, c in hot_cnt.most_common()[-5:]]

    # ── Average sum ────────────────────────────────────────────────────────
    avg_sum = round(
        sum(d["num1"]+d["num2"]+d["num3"]+d["num4"]+d["num5"]+d["num6"] for d in draws) / total,
        1
    )

    conn.close()
    return jsonify({
        "total_draws":       total,
        "first_draw":        {"id": draws[0]["id"],  "date": draws[0]["draw_date"]},
        "last_draw":         {"id": draws[-1]["id"], "date": draws[-1]["draw_date"]},
        "total_possible":    total_possible,
        "coverage_pct":      round(total / total_possible * 100, 4),
        "repeated_combos":   repeated,
        "top_pairs":         top_pairs,
        "top_triplets":      top_triplets,
        "jackpot_count":     len(jackpot_ids),
        "biggest_jackpot":   {"id": biggest["id"], "date": biggest["draw_date"],
                               "prize": biggest["prize"], "winners": biggest["winners"]},
        "most_winners":      {"id": most_w["id"],  "date": most_w["draw_date"],
                               "winners": most_w["winners"], "prize": most_w["prize"]},
        "longest_drought":   best,
        "most_common_prize": {"prize": common_p, "count": common_c},
        "hot":               hot,
        "cold":              cold,
        "avg_sum":           avg_sum,
    })


@app.route("/api/lucky-pick")
def lucky_pick():
    conn = get_db()
    c    = conn.cursor()

    rows = c.execute(
        "SELECT num1,num2,num3,num4,num5,num6,strong_number FROM lotteries"
    ).fetchall()
    conn.close()

    total = len(rows)

    # Build frequency tables from real draw history
    main_freq   = {n: 0 for n in range(1, 38)}
    strong_freq = {n: 0 for n in range(1, 8)}
    for r in rows:
        for col in ("num1","num2","num3","num4","num5","num6"):
            main_freq[r[col]] += 1
        strong_freq[r["strong_number"]] += 1

    # Weighted sampling without replacement for 6 unique main numbers
    pool    = list(range(1, 38))
    weights = [main_freq[n] for n in pool]
    picked  = []
    for _ in range(6):
        chosen = random.choices(pool, weights=weights, k=1)[0]
        idx = pool.index(chosen)
        picked.append(chosen)
        pool.pop(idx)
        weights.pop(idx)
    picked.sort()

    # Weighted pick for strong number
    strong_pool    = list(range(1, 8))
    strong_weights = [strong_freq[n] for n in strong_pool]
    strong         = random.choices(strong_pool, weights=strong_weights, k=1)[0]

    return jsonify({
        "numbers": picked,
        "strong":  strong,
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
