"""
grade_k.py — pull K finals from MLB statsapi and grade the locked K sheet.
==========================================================================
No-peek compliant: reads locked projections (never regenerates), backfills
actual K totals onto the existing lock rows.

Usage:
    python grade_k.py 2026-07-26
    python grade_k.py 2026-07-26 --db hrapp.db     # write to k_ledger
    python grade_k.py 2026-07-26 --json            # dump raw finals

Matching is by MLBAM pitcher ID (exact), so no name-normalization issues.
statsapi is the same open API Gate 2.5 already uses for game logs.
"""

from __future__ import annotations
import json
import sys
import sqlite3
import urllib.request

BASE = "https://statsapi.mlb.com/api/v1"


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "hrapp/1.5"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def slate_gamepks(date: str) -> list[int]:
    d = _get(f"{BASE}/schedule?sportId=1&date={date}")
    pks = []
    for day in d.get("dates", []):
        for g in day.get("games", []):
            # skip suspended/postponed shells with no boxscore
            if g.get("status", {}).get("codedGameState") in ("D", "C"):
                continue
            pks.append(g["gamePk"])
    return pks


def pitcher_k_finals(date: str) -> dict[int, dict]:
    """Return {pitcherId: {'k': int, 'ip': str, 'name': str, 'gamePk': int,
                           'started': bool}} for every pitcher on the slate."""
    out: dict[int, dict] = {}
    for pk in slate_gamepks(date):
        box = _get(f"{BASE}/game/{pk}/boxscore")
        for side in ("home", "away"):
            team = box["teams"][side]
            starters = set(team.get("pitchers", [])[:1])  # first listed = starter
            for key, p in team.get("players", {}).items():
                stats = p.get("stats", {}).get("pitching", {})
                if not stats:
                    continue
                pid = p["person"]["id"]
                entry = {
                    "k": stats.get("strikeOuts", 0),
                    "ip": stats.get("inningsPitched", "0.0"),
                    "name": p["person"].get("fullName", "?"),
                    "gamePk": pk,
                    "started": pid in starters,
                }
                # doubleheaders: keep the appearance with more Ks
                if pid not in out or entry["k"] > out[pid]["k"]:
                    out[pid] = entry
    return out


DDL = """
CREATE TABLE IF NOT EXISTS k_finals (
    slate_date  TEXT NOT NULL,
    pitcher_id  INTEGER NOT NULL,
    name        TEXT,
    k_actual    INTEGER,
    ip          TEXT,
    started     INTEGER,
    game_pk     INTEGER,
    PRIMARY KEY (slate_date, pitcher_id)
);
"""


def write_db(db_path: str, date: str, finals: dict[int, dict]):
    con = sqlite3.connect(db_path)
    con.execute(DDL)
    for pid, e in finals.items():
        con.execute(
            "INSERT OR REPLACE INTO k_finals VALUES (?,?,?,?,?,?,?)",
            (date, pid, e["name"], e["k"], e["ip"],
             1 if e["started"] else 0, e["gamePk"]),
        )
    con.commit()
    con.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: grade_k.py YYYY-MM-DD [--db PATH] [--json]")
    date = sys.argv[1]
    finals = pitcher_k_finals(date)

    if "--json" in sys.argv:
        print(json.dumps(finals, indent=1))
    else:
        for pid, e in sorted(finals.items(), key=lambda x: -x[1]["k"]):
            tag = "S" if e["started"] else "R"
            print(f"{pid:<8} {e['name']:<24} {tag}  K={e['k']:<3} IP={e['ip']}")

    if "--db" in sys.argv:
        db = sys.argv[sys.argv.index("--db") + 1]
        write_db(db, date, finals)
        print(f"\nwrote {len(finals)} rows to k_finals ({db})")

    # Grading join (run against your locked K sheet table):
    #   SELECT l.pitcher_id, l.proj_k, l.range_lo, l.range_hi, f.k_actual,
    #          CASE WHEN f.k_actual BETWEEN l.range_lo AND l.range_hi
    #               THEN 'IN-RANGE' ELSE 'MISS' END AS grade
    #   FROM k_locks l JOIN k_finals f
    #     ON f.pitcher_id = l.pitcher_id AND f.slate_date = l.slate_date;
