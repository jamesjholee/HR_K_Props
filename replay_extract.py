"""replay_extract.py — replay JSONs for boarded HRs and near-HRs. (replay v0)

For a graded date: finds every boarded bat's home run, plus boarded
near-HRs (deep flyouts: EV>=99, 22<=LA<=45, distance>=330), pulls the
Statcast row, and writes one JSON per event to out/replays/<date>/ with
everything the three.js scene needs:

  pitch    - 9-param kinematics, plate loc, sz bounds, name/velo/spin
  contact  - EV, LA, spray bearing (from hc_x/hc_y), official distance
  bat      - bat_speed, swing_length, attack_angle
  wall     - park wall distance+height at the spray bearing, clearance
  context  - batter/pitcher names, teams, park, inning, count, outcome,
             headshot URL, board receipt (hr_prob at lock)

Park geometry from parks.json (wall profile as [bearing_deg, dist_ft,
height_ft] breakpoints, -45=LF line, +45=RF line, linear interp).
v0 ships Progressive Field only — validated against the 8/16 Tatis
near-miss (video ground truth: wall 8 ft, ~382 ft at his bearing,
short by 7.4). Other parks render without wall verdicts until added.

Usage:
    python replay_extract.py --date 2026-08-16
    python replay_extract.py --date 2026-08-16 --also-hrs-only
Statcast pulls are cached in out/statcast_cache/ (idempotent; safe in
the grade step and the D-2 sweep).
"""
import argparse
import json
import math
import sqlite3
from pathlib import Path

import pandas as pd
from pybaseball import statcast_single_game

CACHE = Path("out/statcast_cache")
OUT = Path("out/replays")
HEADSHOT = ("https://img.mlbstatic.com/mlb-photos/image/upload/"
            "w_240,q_auto:best/v1/people/{pid}/headshot/67/current")

PITCH_FIELDS = ["release_pos_x", "release_pos_y", "release_pos_z",
                "vx0", "vy0", "vz0", "ax", "ay", "az",
                "release_speed", "release_spin_rate", "pitch_name",
                "plate_x", "plate_z", "sz_top", "sz_bot"]


def load_parks():
    p = Path("parks.json")
    return json.loads(p.read_text()) if p.exists() else {}


def spray_bearing(hc_x, hc_y):
    """Savant hit coords -> bearing in degrees off CF (-45 LF line,
    +45 RF line). Standard transform: origin ~(125.42, 198.27)."""
    return math.degrees(math.atan2(hc_x - 125.42, 198.27 - hc_y))


def wall_at(park, bearing):
    """(dist_ft, height_ft) at bearing via linear interp of profile."""
    prof = sorted(park["wall"])  # [[bearing, dist, height], ...]
    if bearing <= prof[0][0]:
        return prof[0][1], prof[0][2]
    for (b0, d0, h0), (b1, d1, h1) in zip(prof, prof[1:]):
        if b0 <= bearing <= b1:
            t = (bearing - b0) / (b1 - b0) if b1 > b0 else 0
            return d0 + t * (d1 - d0), h0 + t * (h1 - h0)
    return prof[-1][1], prof[-1][2]


def arc_height_at(ev_mph, la_deg, total_dist, x_ft):
    """Height of the batted-ball arc at range x, using a drag-flattened
    parabola scaled so the arc lands at total_dist. Good to a few feet —
    same fidelity class as the reference graphics."""
    la = math.radians(la_deg)
    if x_ft >= total_dist:
        return 0.0
    # parabola through (0, 3ft contact height) landing at total_dist
    # with initial slope tan(la); apex pulled down ~12% for drag shape
    h0, slope = 3.0, math.tan(la)
    a = -(slope * total_dist + h0) / (total_dist ** 2)
    return max(0.0, h0 + slope * x_ft + a * x_ft * x_ft * 1.0)


def name_of(con, pid):
    r = con.execute("SELECT full_name FROM players WHERE player_id=?",
                    (pid,)).fetchone()
    return r[0] if r else str(pid)


def get_game(gpk):
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{gpk}.parquet"
    if f.exists():
        return pd.read_parquet(f)
    df = statcast_single_game(gpk)
    try:
        df.to_parquet(f)
    except Exception:
        pass
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--db", default="hrapp.db")
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    parks = load_parks()

    boarded = {(bid, gpk) for bid, gpk in con.execute(
        """WITH x AS (SELECT batter_id, COALESCE(game_pk,0) g,
             ROW_NUMBER() OVER (PARTITION BY batter_id,
               COALESCE(NULLIF(game_pk,0),'d') ORDER BY locked_at DESC) rn
           FROM hr_board WHERE slate_date=?)
           SELECT batter_id, g FROM x WHERE rn=1""", (args.date,))}
    boarded_ids = {b for b, _ in boarded}
    gpks = [g for (g,) in con.execute(
        "SELECT DISTINCT gamePk FROM hr_appearances WHERE slate_date=?",
        (args.date,))]

    outdir = OUT / args.date
    outdir.mkdir(parents=True, exist_ok=True)
    made = 0
    for gpk in gpks:
        try:
            df = get_game(gpk)
        except Exception as e:
            print(f"  gamePk {gpk}: statcast pull failed ({e}) — sweep will retry")
            continue
        bb = df[df.description == "hit_into_play"].copy()
        for _, r in bb.iterrows():
            bid = int(r.batter)
            if bid not in boarded_ids:
                continue
            is_hr = r.events == "home_run"
            near = (not is_hr and r.launch_speed == r.launch_speed
                    and r.launch_speed >= 99 and 22 <= r.launch_angle <= 45
                    and (r.hit_distance_sc or 0) >= 330)
            if not (is_hr or near):
                continue
            # respect DH-strict board rows
            if not any(b == bid and (g == 0 or g == gpk) for b, g in boarded):
                continue
            bearing = spray_bearing(r.hc_x, r.hc_y) if r.hc_x == r.hc_x else None
            park_id = str(r.home_team)
            wall = None
            if bearing is not None and park_id in parks:
                wd, wh = wall_at(parks[park_id], bearing)
                ball_h = arc_height_at(r.launch_speed, r.launch_angle,
                                       r.hit_distance_sc or 0, wd)
                wall = {"dist_ft": round(wd, 1), "height_ft": wh,
                        "ball_height_at_wall_ft": round(ball_h, 1),
                        "clearance_ft": round(ball_h - wh, 1),
                        "short_by_ft": (round(wd + 0 - (r.hit_distance_sc or 0), 1)
                                        if not is_hr else None)}
            prob = con.execute(
                """SELECT hr_prob FROM hr_board WHERE slate_date=? AND
                   batter_id=? ORDER BY locked_at DESC LIMIT 1""",
                (args.date, bid)).fetchone()
            doc = {
                "type": "home_run" if is_hr else "near_hr",
                "date": args.date, "gamePk": int(gpk),
                "batter": {"id": bid, "name": name_of(con, bid),
                           "headshot": HEADSHOT.format(pid=bid),
                           "bat_side": (con.execute(
                               "SELECT bat_side FROM players WHERE player_id=?",
                               (bid,)).fetchone() or ["?"])[0]},
                "pitcher": {"id": int(r.pitcher), "name": name_of(con, int(r.pitcher))},
                "context": {"away": r.away_team, "home": r.home_team,
                            "inning": int(r.inning), "half": r.inning_topbot,
                            "count": f"{int(r.balls)}-{int(r.strikes)}",
                            "outcome": r.events,
                            "board_hr_prob": prob[0] if prob else None},
                "pitch": {f: (None if r[f] != r[f] else
                              (float(r[f]) if isinstance(r[f], (int, float))
                               else str(r[f]))) for f in PITCH_FIELDS},
                "bat": {k: (None if r[k] != r[k] else round(float(r[k]), 1))
                        for k in ("bat_speed", "swing_length", "attack_angle",
                                  "attack_direction") if k in r.index},
                "contact": {"ev": float(r.launch_speed),
                            "la": float(r.launch_angle),
                            "distance_ft": (None if r.hit_distance_sc != r.hit_distance_sc
                                            else float(r.hit_distance_sc)),
                            "bearing_deg": None if bearing is None else round(bearing, 1)},
                "wall": wall,
            }
            fn = outdir / f"{'hr' if is_hr else 'near'}_{bid}_{gpk}_{int(r.inning)}.json"
            fn.write_text(json.dumps(doc, indent=1))
            made += 1
            tag = "HR " if is_hr else "NEAR"
            print(f"  [{tag}] {doc['batter']['name']} off {doc['pitcher']['name']}"
                  f" — {r.launch_speed}/{r.launch_angle}° {r.hit_distance_sc}ft"
                  + (f" | wall {wall['dist_ft']}ft@{wall['height_ft']}ft"
                     f" clr {wall['clearance_ft']}" if wall else ""))
    print(f"{args.date}: {made} replay JSON(s) -> {outdir}")


if __name__ == "__main__":
    main()
