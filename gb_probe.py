"""gb_probe.py — does GB%% add HR-suppression signal after the verdict gate?

Run from the hrapp folder (venv active, pybaseball installed):
    python gb_probe.py

Pulls season GB%% for every starter we've ever scored (FanGraphs via
pybaseball, mapped to MLBAM via the Chadwick register), then grades our
own 40-slate sample two ways:
  A) starter HRs allowed per start, by GB tier x verdict
  B) boarded-bat hit rate when facing each GB tier
If GB>=50%% arms suppress even among TARGET verdicts, the flag earns a
shadow lane. Paste the full output back.
"""
import sqlite3
from collections import defaultdict

import requests

con = sqlite3.connect("hrapp.db")


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


pids = [p for (p,) in con.execute(
    "SELECT DISTINCT CAST(pitcher_id AS INT) FROM pitcher_verdicts")]
print(f"{len(pids)} scored arms; pulling Savant batted-ball GB%...")
sv = savant_gb()
gb = {pid: sv[pid] for pid in pids if pid in sv}
print(f"GB% found for {len(gb)}/{len(pids)} arms "
      f"(missing arms are mostly <25 batted balls)")


def tier(pct):
    if pct is None:
        return "unknown"
    return "GB>=50" if pct >= 50 else "GB45-50" if pct >= 45 else \
           "GB40-45" if pct >= 40 else "GB<40"


# A) starter HRs/start by tier x verdict
print("\nA) starter HRs allowed per start, by GB tier (all verdicts, then TARGET-only)")
for only_target in (False, True):
    agg = defaultdict(lambda: [0, 0])
    for d, pid, verdict in con.execute(
            "SELECT DISTINCT slate_date, CAST(pitcher_id AS INT), verdict "
            "FROM pitcher_verdicts WHERE slate_date IN "
            "(SELECT DISTINCT date FROM hr_finals)"):
        if only_target and verdict not in ("TARGET", "TARGET-THIN", "ONE-PITCH"):
            continue
        t = tier(gb.get(pid))
        hrs = con.execute(
            "SELECT COUNT(*) FROM hr_finals WHERE date=? AND "
            "CAST(pitcher_id AS INT)=? AND pitcher_role='S'",
            (d, pid)).fetchone()[0]
        agg[t][0] += 1
        agg[t][1] += hrs
    lbl = "TARGET/ONE-PITCH only" if only_target else "all verdicts"
    print(f"  [{lbl}]")
    for t in ("GB>=50", "GB45-50", "GB40-45", "GB<40", "unknown"):
        n, h = agg[t]
        if n:
            print(f"    {t:8} {n:4} starts  {h:4} HRs  {h/n:.2f}/start")

# B) boarded-bat hit rate vs GB tier of the facing arm (starter-HR-resolved
#    games only — same conditional caveat as prior facing-arm analyses)
print("\nB) boarded-bat hit rate by facing arm's GB tier (TARGET-family arms)")
agg = defaultdict(lambda: [0, 0])
for d, pid, verdict in con.execute(
        "SELECT DISTINCT slate_date, CAST(pitcher_id AS INT), verdict FROM "
        "pitcher_verdicts WHERE verdict IN ('TARGET','TARGET-THIN','ONE-PITCH') "
        "AND slate_date IN (SELECT DISTINCT date FROM hr_finals)"):
    row = con.execute(
        "SELECT gamePk FROM hr_finals WHERE date=? AND "
        "CAST(pitcher_id AS INT)=? AND pitcher_role='S' LIMIT 1",
        (d, pid)).fetchone()
    # resolve his game even without an HR off him: any k_finals start row
    g = row or con.execute(
        "SELECT game_pk FROM k_finals WHERE slate_date=? AND "
        "CAST(pitcher_id AS INT)=? AND started=1", (d, pid)).fetchone()
    if not g or not g[0]:
        continue
    gpk = g[0]
    # his team = team he did NOT face; bats facing him = other team's boarded
    hit_teams = set()
    for bid, in con.execute(
            "SELECT batter_id FROM hr_finals WHERE date=? AND gamePk=? AND "
            "CAST(pitcher_id AS INT)=?", (d, gpk, pid)):
        t = con.execute(
            "SELECT team FROM hr_appearances WHERE slate_date=? AND "
            "gamePk=? AND batter_id=?", (d, gpk, bid)).fetchone()
        if t:
            hit_teams.add(t[0])
    if not hit_teams:
        continue  # facing side unresolvable without an HR — skip (conditional)
    facing = hit_teams.pop()
    t = tier(gb.get(pid))
    for bid, g0, hit in con.execute(
            """WITH x AS (SELECT batter_id, COALESCE(game_pk,0) g,
                 ROW_NUMBER() OVER (PARTITION BY batter_id,
                   COALESCE(NULLIF(game_pk,0),'d') ORDER BY locked_at DESC) rn
               FROM hr_board WHERE slate_date=?)
               SELECT batter_id, g,
                 EXISTS(SELECT 1 FROM hr_finals f WHERE f.date=? AND
                        f.batter_id=x.batter_id AND (x.g=0 OR f.gamePk=x.g))
               FROM x WHERE rn=1 AND g=?""", (d, d, gpk)):
        team = con.execute(
            "SELECT team FROM hr_appearances WHERE slate_date=? AND gamePk=? "
            "AND batter_id=?", (d, gpk, bid)).fetchone()
        if team and team[0] == facing:
            agg[t][0] += 1
            agg[t][1] += hit
for t in ("GB>=50", "GB45-50", "GB40-45", "GB<40", "unknown"):
    n, h = agg[t]
    if n:
        print(f"    {t:8} {h:3}/{n:4} boarded bats = {100*h/n:.1f}%")
print("\n(section B is conditional on games with >=1 starter HR — relative "
      "comparison only, same caveat as the platoon study)")
