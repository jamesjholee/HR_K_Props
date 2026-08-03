"""
track.py — season-to-date performance ledger for HR_K_Props.

Purpose: make "how are we doing" a db-backed, reproducible artifact instead
of a number scattered across grade_*.md files and carry-forward docs.

Creates and maintains `slate_ledger`, one row per slate:
  - computed from hr_board x hr_finals where db coverage exists (source='db')
  - seeded from the historical record for pre-hr_finals slates (source='manual')
  - manual rows are NEVER overwritten by recompute; db rows are refreshed
    every run so a re-grade automatically flows through.

Outputs:
  - out/season_report.md   (human ledger, misses as loud as hits)
  - out/season.json        (machine strip for render.py dashboard header)

Usage:
  python track.py                # refresh ledger + write outputs
  python track.py --db hrapp.db

Wire-in: call at the end of the grade workflow (after grade_hrs), then let
the grade workflow re-render the dashboard so the record strip updates.
Research log, not advice.
"""

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Historical seed rows — slates graded before hr_finals existed (or via the
# old grading source). Numbers are the verified carry-forward record.
# hits/boarded of None marks a locked-but-ungraded slate (shows as PENDING).
# capture fields of None = capture untracked for that slate.
# ---------------------------------------------------------------------------
MANUAL_SEED = [
    # date,        slate#, boarded, hits, slate_hrs, captured, note
    ("2026-07-23", 1, 8, 0, None, None, "pre-board format (promotes 0/8)"),
    ("2026-07-24", 2, None, None, None, None, "LOCKED, UNGRADED — backfill pending"),
    ("2026-07-25", 3, 130, 4, None, None, "old grading source, least trustworthy"),
    ("2026-07-26", 4, 108, 15, 40, 16, "capture 40% (16/40 approx from pct)"),
    ("2026-07-28", 6, 149, 12, 29, 12, "graded pre-hr_finals; 12/29 capture verified"),
]

DDL = """
CREATE TABLE IF NOT EXISTS slate_ledger(
    slate_date     TEXT PRIMARY KEY,
    slate_num      INTEGER,
    config_version TEXT,
    boarded        INTEGER,
    hits           INTEGER,
    slate_hrs      INTEGER,
    captured       INTEGER,
    pen_hrs        INTEGER,
    hits_off_pen   INTEGER,
    source         TEXT,           -- 'db' | 'manual'
    note           TEXT,
    updated_at     TEXT
)
"""

DB_COMPUTE = """
SELECT b.slate_date,
       COUNT(DISTINCT b.batter_id || '-' || COALESCE(NULLIF(b.game_pk,0),'d')) AS boarded,
       COUNT(DISTINCT CASE WHEN f.batter_id IS NOT NULL
                           THEN b.batter_id || '-' || f.gamePk END)            AS hits
FROM hr_board b
LEFT JOIN hr_finals f
       ON f.batter_id = b.batter_id
      AND f.date      = b.slate_date
      AND (b.game_pk = 0 OR b.game_pk = f.gamePk)
GROUP BY b.slate_date
"""

SLATE_TOTALS = """
SELECT date,
       COUNT(*)                                            AS slate_hrs,
       SUM(CASE WHEN pitcher_role='R' THEN 1 ELSE 0 END)   AS pen_hrs
FROM hr_finals GROUP BY date
"""

CAPTURE = """
SELECT f.date,
       COUNT(*)                                             AS captured,
       SUM(CASE WHEN f.pitcher_role='R' THEN 1 ELSE 0 END)  AS hits_off_pen
FROM hr_finals f
WHERE EXISTS (
    SELECT 1 FROM hr_board b
    WHERE b.batter_id = f.batter_id
      AND b.slate_date = f.date
      AND (b.game_pk = 0 OR b.game_pk = f.gamePk)
)
GROUP BY f.date
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def wire_pen_attribution(con):
    """Populate pen_edge.reliever_hrs_allowed / board_hits_off_pen.

    Open-ledger item: columns existed since the pen-edge shadow lane shipped
    but were never written by the grader. Pitching-team derivation:
    hr_board.game is 'AWY@HOME'; hr_finals.half 'top' => home team pitching,
    'bottom' => away. Requires game_pk-populated board rows (v1.6.0+, 7/29+);
    older slates stay NULL rather than guessing.
    """
    pkmap = {}
    for game, pk in con.execute(
        "SELECT DISTINCT game, game_pk FROM hr_board WHERE game_pk > 0"
    ):
        label = game.split(" ")[0].replace("-G1", "").replace("-G2", "")
        if "@" in label:
            away, home = label.split("@", 1)
            pkmap[pk] = (away, home)

    allowed, boarded_hits = {}, {}  # (date, team) -> n
    q = """SELECT f.date, f.gamePk, f.half,
                  EXISTS(SELECT 1 FROM hr_board b
                         WHERE b.batter_id=f.batter_id AND b.slate_date=f.date
                           AND (b.game_pk=0 OR b.game_pk=f.gamePk)) AS was_boarded
           FROM hr_finals f WHERE f.pitcher_role='R'"""
    for date, pk, half, was in con.execute(q):
        if pk not in pkmap:
            continue
        away, home = pkmap[pk]
        pen_team = home if str(half).lower().startswith("top") else away
        allowed[(date, pen_team)] = allowed.get((date, pen_team), 0) + 1
        if was:
            boarded_hits[(date, pen_team)] = boarded_hits.get((date, pen_team), 0) + 1

    graded_dates = {d for (d,) in con.execute("SELECT DISTINCT date FROM hr_finals")}
    n = 0
    for date, team in con.execute("SELECT slate_date, team FROM pen_edge").fetchall():
        if date not in graded_dates:
            continue  # ungraded slates keep NULL, not a misleading 0
        con.execute(
            """UPDATE pen_edge SET reliever_hrs_allowed=?, board_hits_off_pen=?
               WHERE slate_date=? AND team=?""",
            (
                allowed.get((date, team), 0),
                boarded_hits.get((date, team), 0),
                date,
                team,
            ),
        )
        n += 1
    con.commit()
    return n


def refresh(db_path):
    con = sqlite3.connect(db_path)
    con.execute(DDL)

    cfg = {
        d: v for d, v in con.execute("SELECT slate_date, config_version FROM slates")
    }
    finals_dates = {d for (d,) in con.execute("SELECT DISTINCT date FROM hr_finals")}
    totals = {d: (s, p) for d, s, p in con.execute(SLATE_TOTALS)}
    capt = {d: (c, hp) for d, c, hp in con.execute(CAPTURE)}

    # 1) manual seeds (INSERT OR IGNORE — never clobbered)
    for d, num, boarded, hits, shr, cap, note in MANUAL_SEED:
        con.execute(
            """INSERT OR IGNORE INTO slate_ledger
               (slate_date, slate_num, config_version, boarded, hits, slate_hrs,
                captured, pen_hrs, hits_off_pen, source, note, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,'manual',?,?)""",
            (d, num, cfg.get(d), boarded, hits, shr, cap, None, None, note, now()),
        )

    # 2) db-computed rows — refreshed every run, only where hr_finals covers
    #    the date (otherwise a pending/legacy slate would print 0 hits).
    for d, boarded, hits in con.execute(DB_COMPUTE):
        graded = d in finals_dates
        shr, pen = totals.get(d, (None, None))
        cap, hp = capt.get(d, (None, None))
        row = con.execute(
            "SELECT source FROM slate_ledger WHERE slate_date=?", (d,)
        ).fetchone()
        if row and row[0] == "manual":
            continue
        con.execute(
            """INSERT INTO slate_ledger
               (slate_date, slate_num, config_version, boarded, hits, slate_hrs,
                captured, pen_hrs, hits_off_pen, source, note, updated_at)
               VALUES (?,NULL,?,?,?,?,?,?,?,'db',?,?)
               ON CONFLICT(slate_date) DO UPDATE SET
                 config_version=excluded.config_version,
                 boarded=excluded.boarded,   hits=excluded.hits,
                 slate_hrs=excluded.slate_hrs, captured=excluded.captured,
                 pen_hrs=excluded.pen_hrs,   hits_off_pen=excluded.hits_off_pen,
                 note=excluded.note,         updated_at=excluded.updated_at""",
            (
                d,
                cfg.get(d),
                boarded,
                hits if graded else None,
                shr,
                cap,
                pen,
                hp,
                "" if graded else "locked, awaiting grade",
                now(),
            ),
        )

    # 3) renumber slates chronologically (stable public numbering)
    dates = [
        d
        for (d,) in con.execute(
            "SELECT slate_date FROM slate_ledger ORDER BY slate_date"
        )
    ]
    for i, d in enumerate(dates, start=1):
        con.execute("UPDATE slate_ledger SET slate_num=? WHERE slate_date=?", (i, d))

    con.commit()
    return con


def report(con, out_dir):
    rows = con.execute(
        """SELECT slate_num, slate_date, config_version, boarded, hits,
                  slate_hrs, captured, pen_hrs, hits_off_pen, source, note
           FROM slate_ledger ORDER BY slate_date"""
    ).fetchall()

    graded = [r for r in rows if r[4] is not None and r[3]]
    tot_b = sum(r[3] for r in graded)
    tot_h = sum(r[4] for r in graded)

    cur_cfg = graded[-1][2] if graded else None
    cfg_rows = [r for r in graded if r[2] == cur_cfg]
    cfg_b = sum(r[3] for r in cfg_rows)
    cfg_h = sum(r[4] for r in cfg_rows)

    last5 = graded[-5:]
    l5_b = sum(r[3] for r in last5)
    l5_h = sum(r[4] for r in last5)

    pen_known = [r for r in graded if r[5] and r[7] is not None]
    pen_share = (
        sum(r[7] for r in pen_known) / sum(r[5] for r in pen_known)
        if pen_known
        else None
    )

    lines = [
        "# Season Ledger — HR board",
        f"_generated {now()} · research log, not advice · "
        "misses reported as loudly as hits_",
        "",
        "| # | Date | Board | Hits | Hit% | Capture | Pen share | Cfg | Src | Note |",
        "|---|------|------:|-----:|-----:|--------:|----------:|-----|-----|------|",
    ]
    for num, d, cv, b, h, shr, cap, pen, hp, src, note in rows:
        if h is None:
            hitpct, capstr = "—", "PENDING"
        else:
            hitpct = f"{100 * h / b:.1f}%" if b else "—"
            capstr = (
                f"{cap}/{shr} ({100 * cap / shr:.0f}%)"
                if (cap is not None and shr)
                else "—"
            )
        penstr = f"{100 * pen / shr:.0f}%" if (pen is not None and shr) else "—"
        cvs = (cv or "?").replace("v", "", 1).split("-")[0]
        lines.append(
            f"| {num} | {d} | {b if b is not None else '—'} | "
            f"{h if h is not None else '—'} | {hitpct} | {capstr} | {penstr} | "
            f"{cvs} | {src} | {note or ''} |"
        )

    lines += [
        "",
        "## Aggregates",
        f"- **Season:** {tot_h}/{tot_b} = {100 * tot_h / tot_b:.1f}% across {len(graded)} graded slates",
        f"- **Last 5 graded:** {l5_h}/{l5_b} = {100 * l5_h / l5_b:.1f}%",
        f"- **Current config ({cur_cfg}):** {cfg_h}/{cfg_b} = "
        f"{100 * cfg_h / cfg_b:.1f}% across {len(cfg_rows)} slates"
        + (
            " — clean 5-slate sample reached"
            if len(cfg_rows) >= 5
            else f" — {5 - len(cfg_rows)} more for clean 5-slate sample"
        ),
    ]
    if pen_share is not None:
        lines.append(
            f"- **Pen share of slate HRs (db-graded):** {100 * pen_share:.0f}%"
        )
    lines += [
        "",
        "_Typical board breakeven ~15–18%. Season rate below that is not edge;_",
        "_the number above is the honest one._",
    ]

    md = "\n".join(lines) + "\n"
    (Path(out_dir) / "season_report.md").write_text(md)

    strip = {
        "generated": now(),
        "graded_slates": len(graded),
        "season": {
            "hits": tot_h,
            "boarded": tot_b,
            "pct": round(100 * tot_h / tot_b, 1) if tot_b else None,
        },
        "last5": {
            "hits": l5_h,
            "boarded": l5_b,
            "pct": round(100 * l5_h / l5_b, 1) if l5_b else None,
        },
        "current_config": {
            "version": cur_cfg,
            "slates": len(cfg_rows),
            "hits": cfg_h,
            "boarded": cfg_b,
            "pct": round(100 * cfg_h / cfg_b, 1) if cfg_b else None,
        },
        "pen_share_pct": round(100 * pen_share) if pen_share is not None else None,
        "slates": [
            {
                "n": num,
                "date": d,
                "boarded": b,
                "hits": h,
                "pct": round(100 * h / b, 1) if (h is not None and b) else None,
                "pending": h is None and b is None,
            }
            for num, d, cv, b, h, *_ in rows
        ],
    }
    (Path(out_dir) / "season.json").write_text(json.dumps(strip, indent=2))
    return md


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="hrapp.db")
    ap.add_argument("--out", default="out")
    args = ap.parse_args()
    con = refresh(args.db)
    n = wire_pen_attribution(con)
    md = report(con, args.out)
    print(md)
    print(f"[pen attribution] {n} pen_edge rows updated")


if __name__ == "__main__":
    main()
