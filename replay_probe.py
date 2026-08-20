"""replay_probe.py — Statcast field-availability probe for the HR replay build.

Run from the hrapp folder (venv active):
    pip install pybaseball
    python replay_probe.py

Pulls the 2026-08-16 SD@CLE game, locates the Tatis 1st-inning near-HR
flyout (the reference clip) and any HR in the game, and reports which
fields the replay engine needs are actually present and populated:
pitch kinematics (9-param), plate location, exit data, spray coords,
distance, and the new bat-tracking columns. Paste the full output back.
"""
import sqlite3

from pybaseball import statcast_single_game

NEED = [
    # pitch flight (9-param reconstruction)
    "release_pos_x", "release_pos_y", "release_pos_z",
    "vx0", "vy0", "vz0", "ax", "ay", "az",
    "release_speed", "release_spin_rate", "pitch_name",
    "plate_x", "plate_z", "sz_top", "sz_bot",
    # contact + flight out
    "launch_speed", "launch_angle", "hc_x", "hc_y",
    "hit_distance_sc", "events", "description",
    # bat tracking (newer columns — may lag or be absent)
    "bat_speed", "swing_length", "attack_angle", "attack_direction",
    # context
    "balls", "strikes", "inning", "inning_topbot",
    "batter", "pitcher", "home_team", "away_team",
]


def report(row, label):
    print(f"\n=== {label} ===")
    for f in NEED:
        if f in row.index:
            v = row[f]
            ok = "MISSING(NaN)" if v != v else v  # NaN check
            print(f"  {f:20} {ok}")
        else:
            print(f"  {f:20} COLUMN ABSENT")


def main():
    con = sqlite3.connect("hrapp.db")
    gpk = con.execute(
        """SELECT gamePk FROM hr_appearances
           WHERE slate_date='2026-08-16' AND team IN ('SD','CLE') LIMIT 1"""
    ).fetchone()
    if not gpk:
        print("couldn't find SD@CLE 8/16 gamePk in db"); return
    gpk = gpk[0]
    print(f"pulling statcast for gamePk {gpk} (SD@CLE 2026-08-16)...")
    df = statcast_single_game(gpk)
    print(f"rows: {len(df)} | columns: {len(df.columns)}")

    # Tatis' 1st-inning flyout (the reference clip)
    tatis = con.execute(
        "SELECT player_id FROM players WHERE full_name LIKE '%Tatis%'"
    ).fetchone()
    if tatis is not None:
        m = df[(df.batter == tatis[0]) & (df.inning == 1)
               & (df.description == "hit_into_play")]
        if len(m):
            report(m.iloc[0], "TATIS 1st-INNING BATTED BALL (the near-HR)")
        else:
            print("Tatis 1st-inning batted ball not found — paste df columns")

    hrs = df[df.events == "home_run"]
    if len(hrs):
        report(hrs.iloc[0], f"A HOME RUN IN THIS GAME ({len(hrs)} total)")
    else:
        print("no HRs in this game per statcast")

    # latency check: try LAST NIGHT's slate — was it available at grade time?
    print("\n=== latency check: most recent graded date ===")
    last = con.execute("SELECT MAX(date) FROM hr_finals").fetchone()[0]
    g2 = con.execute(
        "SELECT gamePk FROM hr_finals WHERE date=? LIMIT 1", (last,)
    ).fetchone()[0]
    df2 = statcast_single_game(g2)
    hr2 = df2[df2.events == "home_run"]
    bt = hr2["bat_speed"].notna().sum() if "bat_speed" in df2.columns and len(hr2) else 0
    print(f"{last} gamePk {g2}: {len(df2)} pitches, {len(hr2)} HRs, "
          f"bat_speed populated on {bt} of them")


if __name__ == "__main__":
    main()
