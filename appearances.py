"""appearances.py — boxscore appearance grading via MLB statsapi.  (v1.6.3)

Answers "did each boarded bat actually play, and how many PAs did he get"
so the season report can show honest denominators:

  full board   — every locked bat (research/capture identity, unchanged)
  active board — locked bats who took >=1 PA (removes scratched/benched)
  HR per PA    — hits / total PA of active boarded bats vs league ~3.4%

Writes an hr_appearances table: one row per (date, gamePk, batter) for
EVERY batter with batting data in a final boxscore — not just boarded
bats — so capture-side and league-baseline math stays possible later.

No-peek compliant: grading artifact like hr_finals. Never touches
hr_board rows. Locked boards remain byte-identical.

Usage:
  python3 appearances.py --date 2026-08-05
  python3 appearances.py --backfill            # every date in hr_finals
  python3 appearances.py --backfill --db hrapp.db
"""

import argparse
import sqlite3
from datetime import datetime, timezone

# reuse the grader's hardened GET (3 tries w/ backoff) + API constants
from grade_hrs import API, get, slate_games

DDL = """
CREATE TABLE IF NOT EXISTS hr_appearances(
    slate_date  TEXT,
    gamePk      INTEGER,
    batter_id   INTEGER,
    batter      TEXT,
    team        TEXT,
    pa          INTEGER,      -- plate appearances (0 = in box but never batted)
    batting_slot INTEGER,     -- 1-9 lineup slot, NULL if never in the order
    started     INTEGER,      -- 1 = in the starting lineup (battingOrder x00)
    graded_at   TEXT,
    UNIQUE(slate_date, gamePk, batter_id)
)
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def game_appearances(game_pk):
    """All batters with batting data in a final boxscore."""
    box = get(f"{API}/game/{game_pk}/boxscore")
    rows = []
    for side in ("away", "home"):
        tm = (box.get("teams", {}) or {}).get(side, {}) or {}
        team = ((tm.get("team", {}) or {}).get("abbreviation")
                or (tm.get("team", {}) or {}).get("name", "?"))
        for key, p in (tm.get("players", {}) or {}).items():
            person = p.get("person", {}) or {}
            pid = person.get("id")
            if not pid:
                continue
            bat = ((p.get("stats", {}) or {}).get("batting", {}) or {})
            order = p.get("battingOrder")  # e.g. "300" starter, "301" sub
            pa = int(bat.get("plateAppearances") or 0)
            if not order and pa == 0:
                continue  # pitcher / never-appeared bench body with no order
            slot = int(order) // 100 if order else None
            started = 1 if (order and str(order).endswith("00")) else 0
            rows.append(
                {
                    "gamePk": game_pk,
                    "batter_id": pid,
                    "batter": person.get("fullName"),
                    "team": team,
                    "pa": pa,
                    "batting_slot": slot,
                    "started": started,
                }
            )
    return rows


def write_appearances(con, date, rows):
    con.execute(DDL)
    ts = now()
    for r in rows:
        con.execute(
            """INSERT INTO hr_appearances
               (slate_date, gamePk, batter_id, batter, team, pa,
                batting_slot, started, graded_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(slate_date, gamePk, batter_id) DO UPDATE SET
                 batter=excluded.batter, team=excluded.team, pa=excluded.pa,
                 batting_slot=excluded.batting_slot, started=excluded.started,
                 graded_at=excluded.graded_at""",
            (date, r["gamePk"], r["batter_id"], r["batter"], r["team"],
             r["pa"], r["batting_slot"], r["started"], ts),
        )
    con.commit()


def run_date(con, date):
    games = slate_games(date)
    finals = [
        g for g in games
        if g["status"] == "Final"
        and g["detailed"] not in ("Postponed", "Cancelled", "Suspended")
    ]
    total, failed = 0, 0
    for g in finals:
        try:
            rows = game_appearances(g["gamePk"])
            write_appearances(con, date, rows)
            total += len(rows)
        except Exception as e:  # one bad game never kills the run
            failed += 1
            print(f"  ⚠ boxscore pull failed for {g['gamePk']}: {e}")
    print(f"{date}: {len(finals)} final games, {total} appearance rows"
          + (f", {failed} FAILED pulls" if failed else ""))
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--backfill", action="store_true",
                    help="run every date present in hr_finals")
    ap.add_argument("--db", default="hrapp.db")
    args = ap.parse_args()
    if not args.date and not args.backfill:
        ap.error("need --date or --backfill")

    con = sqlite3.connect(args.db)
    con.execute(DDL)
    if args.backfill:
        dates = [d for (d,) in con.execute(
            "SELECT DISTINCT date FROM hr_finals ORDER BY date")]
        print(f"=== appearance backfill — {len(dates)} graded dates ===")
        for d in dates:
            run_date(con, d)
    else:
        run_date(con, args.date)


if __name__ == "__main__":
    main()
