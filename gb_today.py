"""gb_today.py — GB%% tier for every arm on a locked slate.

    python gb_today.py                    # today's slate (ET)
    python gb_today.py --date 2026-09-04

Pulls season GB%% from FanGraphs (pybaseball), maps to MLBAM, prints each
scored arm with tier: EXTREME-GB (>=55), GB (50-55), LEAN-GB (45-50),
NEUTRAL, FLY (<40). Interpretation per the working hypothesis (pending
gb_probe validation): GB/EXTREME arms suppress HRs regardless of verdict —
treat their opposing lineups with caution even when the gate says TARGET.
"""
import argparse
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import requests



def savant_gb():
    """GB%% per pitcher from Savant's batted-ball leaderboard CSV.
    Keyed on MLBAM id directly — no FanGraphs, no id mapping.
    Returns {mlbam_id: gb_pct}. Prints columns if the shape surprises us."""
    import io, csv, requests
    url = ("https://baseballsavant.mlb.com/leaderboard/batted-ball"
           "?type=pitcher&season[]=2026&csv=true")   # params per the page's own Download CSV link
    r = requests.get(url, timeout=60,
                     headers={"User-Agent": "Mozilla/5.0 (hrapp research)"})
    r.raise_for_status()
    text = r.content.decode("utf-8-sig")   # strip Savant's BOM
    rows = list(csv.DictReader(io.StringIO(text)))
    rows = [{k.strip().strip(chr(34)).strip(): v for k, v in row.items()}
            for row in rows]
    if not rows:
        raise SystemExit("savant CSV came back empty")
    cols = rows[0].keys()
    idc = next((c for c in cols if c.lower() in
                ("id", "player_id", "pitcher", "pitcher_id", "entity_id")), None)
    gbc = next((c for c in cols if "gb" in c.lower()
                and ("percent" in c.lower() or "%" in c or c.lower() == "gb_rate")), None)
    if not idc or not gbc:
        print("unexpected savant columns:", list(cols))
        raise SystemExit("paste the column list above back to Claude")
    def clean(x):
        return str(x).strip().strip(chr(34)).replace("%", "").strip()
    out, skipped = {}, 0
    for row in rows:
        try:
            if "year" in row and clean(row["year"]) not in ("", "2026"):
                continue
            v = float(clean(row[gbc]))
            out[int(float(clean(row[idc])))] = v * (100 if v <= 1 else 1)
        except (ValueError, TypeError):
            skipped += 1
    print(f"  savant: {len(rows)} rows -> {len(out)} pitchers parsed"
          f" ({skipped} skipped); sample: {dict(list(out.items())[:2])}")
    if not out and rows:
        print("  first raw row for debugging:", dict(list(rows[0].items())[:6]))
    return out


def tier(p):
    return ("EXTREME-GB" if p >= 55 else "GB" if p >= 50 else
            "LEAN-GB" if p >= 45 else "NEUTRAL" if p >= 40 else "FLY")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now(
        ZoneInfo("America/New_York")).strftime("%F"))
    ap.add_argument("--db", default="hrapp.db")
    a = ap.parse_args()
    con = sqlite3.connect(a.db)
    arms = con.execute(
        "SELECT DISTINCT pitcher_name, CAST(pitcher_id AS INT), verdict "
        "FROM pitcher_verdicts WHERE slate_date=?", (a.date,)).fetchall()
    if not arms:
        print(f"no scored arms for {a.date} — has the board locked?")
        return
    sv = savant_gb()
    rows = []
    for name, pid, verdict in arms:
        pct = sv.get(pid)
        rows.append((pct if pct is not None else -1, name, verdict, pct))
    print(f"{a.date} — arms by GB%:")
    for _, name, verdict, pct in sorted(rows, reverse=True):
        t = "no-GB-data" if pct is None else tier(pct)
        warn = "  <-- HR-suppressor despite verdict" if pct and pct >= 50 \
               and verdict in ("TARGET", "TARGET-THIN", "ONE-PITCH") else ""
        print(f"  {name:24} {verdict:15} GB% "
              f"{('%4.1f' % pct) if pct is not None else '  — '}  {t}{warn}")


if __name__ == "__main__":
    main()
