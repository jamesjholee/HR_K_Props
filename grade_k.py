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
import sqlite3
import sys
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
            # skip postponed/cancelled shells and suspended (incomplete)
            # games — suspended totals are partial and must not grade as
            # final; rerun after the resumption completes. (7/28 fix)
            st = g.get("status", {}) or {}
            if st.get("codedGameState") in ("D", "C", "U"):
                continue
            if (st.get("detailedState") or "").startswith("Suspended"):
                continue
            pks.append(g["gamePk"])
    return pks


def pitcher_k_finals(date: str) -> dict[tuple, dict]:
    """Return {(pitcherId, gamePk): {'k','ip','name','gamePk','started'}}
    for every pitching appearance on the slate (DH-safe)."""
    out: dict[tuple, dict] = {}
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
                # 7/28 fix: doubleheaders — keep EVERY appearance,
                # keyed by (pid, gamePk). Old code kept only the max-K
                # appearance, undercounting relievers who work both ends.
                out[(pid, pk)] = entry
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
    PRIMARY KEY (slate_date, pitcher_id, game_pk)
);
"""


def _migrate_pk(con):
    """One-time: rebuild k_finals if it still has the old 2-column PK
    (pre-7/28). Preserves existing rows."""
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='k_finals'"
    ).fetchone()
    if row and "PRIMARY KEY (slate_date, pitcher_id)" in (row[0] or ""):
        con.executescript(
            "ALTER TABLE k_finals RENAME TO k_finals_old;"
            + DDL
            + "INSERT OR IGNORE INTO k_finals SELECT * FROM k_finals_old;"
            "DROP TABLE k_finals_old;"
        )


def write_db(db_path: str, date: str, finals: dict):
    con = sqlite3.connect(db_path)
    con.execute(DDL)
    _migrate_pk(con)
    for (pid, pk), e in finals.items():
        con.execute(
            "INSERT OR REPLACE INTO k_finals VALUES (?,?,?,?,?,?,?)",
            (date, pid, e["name"], e["k"], e["ip"], 1 if e["started"] else 0, pk),
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
        for (pid, pk), e in sorted(finals.items(), key=lambda x: -x[1]["k"]):
            tag = "S" if e["started"] else "R"
            print(f"{pid:<8} {e['name']:<24} {tag}  K={e['k']:<3} IP={e['ip']}  g{pk}")

    if "--db" in sys.argv:
        db = sys.argv[sys.argv.index("--db") + 1]
        write_db(db, date, finals)
        print(f"\nwrote {len(finals)} rows to k_finals ({db})")

    # Grading join vs the real lock table (k_paper). Two rules:
    #   1. STARTERS ONLY (f.started=1) — the sheet projects starts.
    #   2. LATEST LOCK ONLY — reruns write duplicate projection rows;
    #      joining them all fans out the accuracy math.
    #   SELECT l.pitcher_id, l.pitcher_name, l.proj_k, l.lo, l.hi,
    #          f.k_actual,
    #          CASE WHEN f.k_actual BETWEEN l.lo AND l.hi
    #               THEN 'IN-RANGE' ELSE 'MISS' END AS grade
    #   FROM k_paper l
    #   JOIN k_finals f ON f.pitcher_id = l.pitcher_id
    #                  AND f.slate_date = l.slate_date AND f.started = 1
    #   WHERE l.locked_at = (SELECT MAX(l2.locked_at) FROM k_paper l2
    #                        WHERE l2.pitcher_id = l.pitcher_id
    #                          AND l2.slate_date = l.slate_date);
