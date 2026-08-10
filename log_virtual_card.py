"""log_virtual_card.py — freeze selection-layer virtual cards at lock. (v1.6.5)

Writes the top-30 and top-60 (by locked hr_prob, slate-wide, deduped) into a
`virtual_cards` table immediately after the morning lock. The cards are a
deterministic function of the locked board, but logging them forward with a
timestamp is what makes the selection layer's validation OUT-OF-SAMPLE: the
cut rule (top-N by hr_prob) is hereby frozen as of v1.6.5, and every card
row proves the slice was named before the games were played.

Immutable like the board: INSERT OR IGNORE — first write wins, re-runs are
no-ops. No ranking-weight change; the board itself is untouched.

Usage:
  python3 log_virtual_card.py --date 2026-08-10
"""

import argparse
import sqlite3
from datetime import datetime, timezone

CARDS = {"top30": 30, "top60": 60}

DDL = """
CREATE TABLE IF NOT EXISTS virtual_cards(
    slate_date TEXT,
    card       TEXT,
    card_rank  INTEGER,
    batter_id  INTEGER,
    batter     TEXT,
    game_pk    INTEGER,
    hr_prob    REAL,
    locked_at  TEXT,
    UNIQUE(slate_date, card, batter_id, game_pk)
)
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--db", default="hrapp.db")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.execute(DDL)

    rows = con.execute(
        """WITH d AS (
             SELECT batter_id, batter_name, COALESCE(game_pk,0) gpk, hr_prob,
                    ROW_NUMBER() OVER (
                      PARTITION BY batter_id, COALESCE(NULLIF(game_pk,0),'d')
                      ORDER BY locked_at DESC) rn
             FROM hr_board WHERE slate_date=?)
           SELECT batter_id, batter_name, gpk, hr_prob
           FROM d WHERE rn=1 ORDER BY hr_prob DESC, batter_id""",
        (args.date,),
    ).fetchall()
    if not rows:
        print(f"{args.date}: no locked board — no card logged")
        return

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for card, n in CARDS.items():
        wrote = 0
        for rank, (bid, name, gpk, prob) in enumerate(rows[:n], start=1):
            cur = con.execute(
                """INSERT OR IGNORE INTO virtual_cards
                   (slate_date, card, card_rank, batter_id, batter, game_pk,
                    hr_prob, locked_at) VALUES (?,?,?,?,?,?,?,?)""",
                (args.date, card, rank, bid, name, gpk, prob, ts),
            )
            wrote += cur.rowcount
        total = min(n, len(rows))
        print(f"{args.date} {card}: {wrote} rows written"
              + ("" if wrote == total else
                 f" ({total - wrote} already frozen — card immutable)"))
    con.commit()


if __name__ == "__main__":
    main()
