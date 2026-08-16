"""backfill_hands.py — player handedness dimension via MLB statsapi. (v1.6.7)

Builds a `players` table (bat_side L/R/S, pitch_hand L/R) for every player
id that appears anywhere in the db (board, appearances, finals, verdicts,
K tables), and exports out/hands.json for the dashboard's handedness tags
and filters. Static biographical data — one big backfill, then nightly
--missing-only sweeps catch call-ups.

Feeds Stage 2 of the bat-opportunity research: HR/PA inside TARGET games
split by platoon matchup x arm damage-vector class.

Usage:
  python3 backfill_hands.py --backfill        # every id in the db
  python3 backfill_hands.py --missing-only    # only ids not yet in players
"""

import argparse
import json
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from grade_hrs import API, get

DDL = """
CREATE TABLE IF NOT EXISTS players(
    player_id  INTEGER PRIMARY KEY,
    full_name  TEXT,
    bat_side   TEXT,     -- L / R / S
    pitch_hand TEXT,     -- L / R
    updated_at TEXT
)
"""

ID_SOURCES = [
    "SELECT DISTINCT batter_id FROM hr_board",
    "SELECT DISTINCT batter_id FROM hr_appearances",
    "SELECT DISTINCT batter_id FROM hr_finals",
    "SELECT DISTINCT CAST(pitcher_id AS INT) FROM hr_finals",
    "SELECT DISTINCT CAST(pitcher_id AS INT) FROM pitcher_verdicts",
    "SELECT DISTINCT CAST(pitcher_id AS INT) FROM k_paper",
]


def norm(s):
    return unicodedata.normalize("NFKD", s or "").encode(
        "ascii", "ignore").decode().lower().strip()


def all_ids(con):
    ids = set()
    for q in ID_SOURCES:
        try:
            ids.update(i for (i,) in con.execute(q) if i)
        except sqlite3.Error:
            pass
    return ids


def fetch_people(ids):
    """statsapi people lookup, batched."""
    out = []
    ids = sorted(ids)
    for i in range(0, len(ids), 100):
        batch = ids[i:i + 100]
        data = get(f"{API}/people?personIds={','.join(map(str, batch))}")
        for p in data.get("people", []):
            out.append({
                "player_id": p["id"],
                "full_name": p.get("fullName"),
                "bat_side": (p.get("batSide", {}) or {}).get("code"),
                "pitch_hand": (p.get("pitchHand", {}) or {}).get("code"),
            })
    return out


def export_json(con):
    """out/hands.json — name-keyed for the dashboard overlay."""
    d = {}
    for pid, name, b, p in con.execute(
            "SELECT player_id, full_name, bat_side, pitch_hand FROM players"):
        if name:
            d[norm(name)] = {"b": b or "?", "p": p or "?"}
    Path("out").mkdir(exist_ok=True)
    Path("out/hands.json").write_text(json.dumps(d, ensure_ascii=False))
    return len(d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--missing-only", action="store_true")
    ap.add_argument("--db", default="hrapp.db")
    args = ap.parse_args()
    if not (args.backfill or args.missing_only):
        ap.error("need --backfill or --missing-only")

    con = sqlite3.connect(args.db)
    con.execute(DDL)
    want = all_ids(con)
    if args.missing_only:
        have = {i for (i,) in con.execute("SELECT player_id FROM players")}
        want -= have
    print(f"fetching {len(want)} players...")
    if want:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for r in fetch_people(want):
            con.execute(
                """INSERT INTO players VALUES (?,?,?,?,?)
                   ON CONFLICT(player_id) DO UPDATE SET
                     full_name=excluded.full_name, bat_side=excluded.bat_side,
                     pitch_hand=excluded.pitch_hand, updated_at=excluded.updated_at""",
                (r["player_id"], r["full_name"], r["bat_side"],
                 r["pitch_hand"], ts))
        con.commit()
    n = export_json(con)
    print(f"players table current; hands.json exported ({n} names)")


if __name__ == "__main__":
    main()
