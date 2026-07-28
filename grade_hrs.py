"""grade_hrs.py — evening HR grader via MLB statsapi.  (v1)

Pulls every HR event for the slate date directly from MLB's statsapi
play-by-play: batter MLBAM id, pitcher MLBAM id, inning, and a
starter/reliever tag per event. Writes an hr_finals table into hrapp.db
and (if the locked board table can be located) grades the board by
joining on BATTER ID — no name matching, so the Peña/Suárez accent bug
class cannot recur here.

Replaces the onlyhomers.com scrape/paste workflow for grading.
No-peek compliant: this only BACKFILLS results onto existing locks;
it never touches board rows.

Usage:
  python3 grade_hrs.py --date 2026-07-27
  python3 grade_hrs.py --date 2026-07-27 --db hrapp.db --out out/grades
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

import requests

API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "hr-engine-grader/1.0"}


def get(url, **params):
    r = requests.get(url, params=params, headers=UA, timeout=30)
    r.raise_for_status()
    return r.json()


def slate_games(date):
    """Final/completed gamePks for the date, with team codes."""
    sched = get(f"{API}/schedule", sportId=1, date=date)
    games = []
    for d in sched.get("dates", []):
        for g in d.get("games", []):
            status = (g.get("status", {}) or {}).get("abstractGameState", "")
            games.append(
                {
                    "gamePk": g["gamePk"],
                    "status": status,  # Preview / Live / Final
                    "detailed": (g.get("status", {}) or {}).get("detailedState", ""),
                    "away": g["teams"]["away"]["team"].get("name", "?"),
                    "home": g["teams"]["home"]["team"].get("name", "?"),
                }
            )
    return games


def starters_for_game(game_pk):
    """{pitcher_id} set of STARTERS (first pitcher listed per side)."""
    box = get(f"{API}/game/{game_pk}/boxscore")
    starters = set()
    for side in ("away", "home"):
        pitchers = (box.get("teams", {}).get(side, {}) or {}).get("pitchers", [])
        if pitchers:
            starters.add(pitchers[0])
    return starters


def hr_events(game_pk, starters):
    """All HR events in a game via play-by-play, tagged S/R."""
    pbp = get(f"{API}/game/{game_pk}/playByPlay")
    out = []
    for play in pbp.get("allPlays", []):
        if (play.get("result", {}) or {}).get("event") != "Home Run":
            continue
        mu = play.get("matchup", {}) or {}
        bat, pit = mu.get("batter", {}) or {}, mu.get("pitcher", {}) or {}
        about = play.get("about", {}) or {}
        out.append(
            {
                "gamePk": game_pk,
                "inning": about.get("inning"),
                "half": about.get("halfInning"),
                "batter_id": bat.get("id"),
                "batter": bat.get("fullName"),
                "pitcher_id": pit.get("id"),
                "pitcher": pit.get("fullName"),
                "pitcher_role": "S" if pit.get("id") in starters else "R",
            }
        )
    return out


def write_finals(db_path, date, events):
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE IF NOT EXISTS hr_finals(
        date TEXT, gamePk INTEGER, inning INTEGER, half TEXT,
        batter_id INTEGER, batter TEXT, pitcher_id INTEGER, pitcher TEXT,
        pitcher_role TEXT, graded_at TEXT,
        UNIQUE(date, gamePk, inning, half, batter_id, pitcher_id))""")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for e in events:
        con.execute(
            """INSERT OR IGNORE INTO hr_finals
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                date,
                e["gamePk"],
                e["inning"],
                e["half"],
                e["batter_id"],
                e["batter"],
                e["pitcher_id"],
                e["pitcher"],
                e["pitcher_role"],
                now,
            ),
        )
    con.commit()
    return con


def find_board_table(con, date):
    """Locate the locked-board table by shape, not by assumed name:
    a table with a date-like column plus an integer player-id column."""
    candidates = []
    for (tname,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        if tname == "hr_finals":
            continue
        cols = [c[1].lower() for c in con.execute(f"PRAGMA table_info({tname})")]
        has_date = any(c in ("date", "slate", "slate_date") for c in cols)
        id_cols = [
            c
            for c in cols
            if c in ("player_id", "batter_id", "bat_id", "id", "mlbam_id", "pid")
        ]
        name_cols = [c for c in cols if "name" in c or c == "bat"]
        if has_date and id_cols and name_cols:
            try:
                n = con.execute(
                    f"SELECT COUNT(*) FROM {tname} WHERE date=?", (date,)
                ).fetchone()[0]
            except sqlite3.Error:
                continue
            if n:
                candidates.append((tname, id_cols[0], name_cols[0], n))
    return candidates


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--db", default="hrapp.db")
    ap.add_argument("--out", default="out/grades")
    args = ap.parse_args()
    date = args.date

    games = slate_games(date)
    finals = [
        g
        for g in games
        if g["status"] == "Final"
        and g["detailed"] not in ("Postponed", "Cancelled", "Suspended")
    ]
    voided = [g for g in games if g["detailed"] in ("Postponed", "Cancelled")]
    pending = [g for g in games if g not in finals and g not in voided]
    print(
        f"=== HR grade — {date} — {len(games)} games "
        f"({len(finals)} final, {len(voided)} VOID, {len(pending)} not final) ==="
    )
    for g in voided:
        print(
            f"  ∅ VOID: {g['away']} @ {g['home']} — {g['detailed']} — "
            f"exclude this game's board rows from all denominators (props refund)"
        )
    for g in pending:
        print(
            f"  ⚠ NOT FINAL: {g['away']} @ {g['home']} — {g['detailed']}"
            f" (grade is PARTIAL; rerun later)"
        )

    events = []
    for g in finals:
        try:
            starters = starters_for_game(g["gamePk"])
            evs = hr_events(g["gamePk"], starters)
            events.extend(evs)
            print(f"  {g['away']} @ {g['home']}: {len(evs)} HR")
        except Exception as e:
            print(f"  ⚠ pull failed for {g['gamePk']}: {e}")

    n_s = sum(1 for e in events if e["pitcher_role"] == "S")
    n_r = len(events) - n_s
    pct_r = (n_r / len(events) * 100) if events else 0.0
    print(
        f"\nSlate total: {len(events)} HR — {n_s} starter / {n_r} reliever "
        f"({pct_r:.0f}% off pens)"
    )

    # persist raw events (JSON audit + db table)
    os.makedirs(args.out, exist_ok=True)
    jpath = os.path.join(args.out, f"hr_events_{date}.json")
    with open(jpath, "w") as f:
        json.dump(events, f, indent=1)
    con = write_finals(args.db, date, events)
    print(f"hr_finals written to {args.db}; audit JSON: {jpath}")

    # ---- board grading: join on BATTER ID ----
    hr_ids = {e["batter_id"] for e in events}
    boards = find_board_table(con, date)
    if len(boards) == 1:
        tname, idcol, namecol, nrows = boards[0]
        rows = con.execute(
            f"SELECT DISTINCT {idcol}, {namecol} FROM {tname} WHERE date=?", (date,)
        ).fetchall()
        hits = [(pid, nm) for pid, nm in rows if pid in hr_ids]
        boarded_ids = {pid for pid, _ in rows}
        captured = [e for e in events if e["batter_id"] in boarded_ids]
        print(f"\nBoard table: {tname} ({nrows} rows / {len(rows)} unique bats)")
        print(
            f"PROP HITS: {len(hits)}/{len(rows)} boarded bats homered "
            f"({(len(hits) / len(rows) * 100) if rows else 0:.1f}%)"
        )
        print(
            f"HR CAPTURE: {len(captured)}/{len(events)} slate HR events "
            f"boarded ({(len(captured) / len(events) * 100) if events else 0:.0f}%)"
        )
        pen_hits = [e for e in captured if e["pitcher_role"] == "R"]
        print(
            f"  of captured: {len(captured) - len(pen_hits)} off starters, "
            f"{len(pen_hits)} off pens"
        )
        for pid, nm in hits:
            ev = next(e for e in events if e["batter_id"] == pid)
            print(f"  ✓ {nm} — HR off {ev['pitcher']} ({ev['pitcher_role']})")
    elif boards:
        print(
            f"\n⚠ Multiple board-shaped tables found: "
            f"{[b[0] for b in boards]} — grade manually or pin the table "
            f"name in this script."
        )
    else:
        print(
            "\n⚠ No board table located for this date — hr_finals written; "
            "join manually via: SELECT ... JOIN hr_finals USING(date, <id>)."
        )

    if pending:
        sys.exit(3)  # nonzero-ish signal for CI: partial grade


if __name__ == "__main__":
    main()
