"""
relievers.py — Bullpen fatigue framework (hrapp v1.5, SHADOW MODE)
==================================================================
Data source: /MLB/bullpen-usage (OPEN endpoint, no cookie required).

STATUS: shadow/annotation only. Produces PEN-EDGE tags per game side and
logs reliever-HR attribution. ZERO ranking-weight changes. Own ledger
table (pen_edge). Grade 5-6 slates before any tag earns weight.

Motivation: 7/25 slate — ~7 of 14 HRs off relievers (~50%); 2 of our 4
board hits were reliever HRs the starter-first model can't explain.
Documented range: 26-45% of slate HRs off relievers.

FEED CAVEATS (verified 2026-07-26):
  1. teams array has 32 entries: filter out teamCode in ('AL','NL')
     (All-Star pseudo-rosters).
  2. relievers array is contaminated with start-shaped workloads
     (bulk arms, spot starters, and at least one outright mislabel:
     Mikolas listed in the WSH pen). DE-NOISE: exclude any arm whose max
     single-day pitch count >= START_SHAPE_CUTOFF from fatigue math and
     recompute team totals locally. Never trust teamTotalLast3/5 raw.
  3. Some reliever dicts omit last3Total when the arm didn't pitch in
     the L3 window — use .get(..., 0) everywhere.
  4. threePlusConsecutiveDays exists but may be empty — wire it anyway.
  5. thresholds dict ships in the feed; use feed values, keep local
     fallbacks in case the key disappears.
"""

from __future__ import annotations
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

# ---------------------------------------------------------------- config
PSEUDO_TEAMS = {"AL", "NL"}
START_SHAPE_CUTOFF = 50   # single-day pitches >= this => start-shaped, exclude
FALLBACK_THRESHOLDS = {
    "dailyPitches":   {"warning": 23, "danger": 33, "cap": 43},
    "relieverLast3":  {"warning": 31, "danger": 42, "cap": 50},
    "relieverLast5":  {"warning": 41, "danger": 52, "cap": 61},
    "teamLast3":      {"warning": 196, "danger": 239, "cap": 258},
    "teamLast5":      {"warning": 306, "danger": 356, "cap": 393},
}
# PEN-EDGE scoring weights (shadow display only, not probability inputs)
W_TEAM_TIER   = {"fresh": 0, "WARNING": 1, "DANGER": 2, "CAP": 3}
W_B2B_ARM     = 1          # per back-to-back arm
W_3PLUS_ARM   = 2          # per 3+ consecutive-day arm
W_TIRED_ARM   = 1          # per individually WARNING+ arm (post-de-noise)
EDGE_FLOOR    = 3          # score >= floor => PEN-EDGE tag emitted


def _tier(val: float, t: dict) -> str:
    if val >= t["cap"]:     return "CAP"
    if val >= t["danger"]:  return "DANGER"
    if val >= t["warning"]: return "WARNING"
    return "fresh"


@dataclass
class PenState:
    team: str
    opponent: str
    game_time: str
    l3_raw: int
    l5_raw: int
    l3_clean: int
    l5_clean: int
    tier_l3: str
    tier_l5: str
    trend: str
    excluded_arms: list = field(default_factory=list)   # start-shaped, dropped
    tired_arms: list = field(default_factory=list)      # (name, hand, flags)
    b2b_arms: list = field(default_factory=list)        # back-to-back names
    three_plus_arms: list = field(default_factory=list)
    edge_score: int = 0

    @property
    def tag(self) -> str | None:
        """PEN-EDGE tag for the OPPOSING lineup, or None."""
        if self.edge_score >= EDGE_FLOOR:
            return f"PEN-EDGE:{self.team}(score={self.edge_score})"
        return None


def load_bullpen(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def analyze(data: dict) -> dict[str, PenState]:
    th = {**FALLBACK_THRESHOLDS, **data.get("thresholds", {})}
    b2b = {}
    for p in data.get("twoConsecutiveDays", []):
        b2b.setdefault(p["teamCode"], []).append(p["playerName"])
    three_plus = {}
    for p in data.get("threePlusConsecutiveDays", []):
        three_plus.setdefault(p["teamCode"], []).append(p["playerName"])

    out: dict[str, PenState] = {}
    for t in data["teams"]:
        code = t["teamCode"]
        if code in PSEUDO_TEAMS:
            continue

        clean3 = clean5 = 0
        excluded, tired = [], []
        for r in t["relievers"]:
            daily = r.get("dailyPitchCounts", [])
            r3 = r.get("last3Total", 0)
            r5 = r.get("last5Total", 0)
            if daily and max(daily) >= START_SHAPE_CUTOFF:
                excluded.append((r["playerName"], max(daily)))
                continue  # start-shaped: out of fatigue math entirely
            clean3 += r3
            clean5 += r5
            flags = []
            t3 = _tier(r3, th["relieverLast3"])
            t5 = _tier(r5, th["relieverLast5"])
            if t3 != "fresh": flags.append(f"L3:{r3}({t3})")
            if t5 != "fresh": flags.append(f"L5:{r5}({t5})")
            if r.get("daysRest", 9) == 0: flags.append("0-rest")
            if flags:
                tired.append((r["playerName"], r.get("pitchingHand", "?"), flags))

        ps = PenState(
            team=code,
            opponent=t.get("todayOpponent", "?"),
            game_time=t.get("todayGameTime", "?"),
            l3_raw=t.get("teamTotalLast3", 0),
            l5_raw=t.get("teamTotalLast5", 0),
            l3_clean=clean3,
            l5_clean=clean5,
            tier_l3=_tier(clean3, th["teamLast3"]),
            tier_l5=_tier(clean5, th["teamLast5"]),
            trend=t.get("trend", "?"),
            excluded_arms=excluded,
            tired_arms=tired,
            b2b_arms=b2b.get(code, []),
            three_plus_arms=three_plus.get(code, []),
        )
        ps.edge_score = (
            max(W_TEAM_TIER[ps.tier_l3], W_TEAM_TIER[ps.tier_l5])
            + W_B2B_ARM * len(ps.b2b_arms)
            + W_3PLUS_ARM * len(ps.three_plus_arms)
            + W_TIRED_ARM * len(ps.tired_arms)
        )
        out[code] = ps
    return out


# ---------------------------------------------------------------- ledger
DDL = """
CREATE TABLE IF NOT EXISTS pen_edge (
    slate_date      TEXT NOT NULL,
    team            TEXT NOT NULL,           -- the taxed pen
    beneficiary     TEXT NOT NULL,           -- opposing lineup
    edge_score      INTEGER NOT NULL,
    tier_l3         TEXT, tier_l5 TEXT,
    l3_clean        INTEGER, l5_clean INTEGER,
    b2b_arms        TEXT,                    -- json list
    tired_arms      TEXT,                    -- json list
    excluded_arms   TEXT,                    -- json list (audit trail)
    tagged          INTEGER NOT NULL,        -- 1 if PEN-EDGE emitted
    locked_at       TEXT NOT NULL,
    -- graded after the slate (backfill onto lock, no-peek compliant):
    reliever_hrs_allowed  INTEGER,           -- HRs off this pen tonight
    board_hits_off_pen    INTEGER,           -- our board bats that homered off it
    PRIMARY KEY (slate_date, team)
);
"""

def write_ledger(db_path: str, slate_date: str, states: dict[str, PenState]):
    con = sqlite3.connect(db_path)
    con.execute(DDL)
    now = datetime.now(timezone.utc).isoformat()
    for ps in states.values():
        con.execute(
            """INSERT OR IGNORE INTO pen_edge
               (slate_date, team, beneficiary, edge_score, tier_l3, tier_l5,
                l3_clean, l5_clean, b2b_arms, tired_arms, excluded_arms,
                tagged, locked_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (slate_date, ps.team, ps.opponent, ps.edge_score,
             ps.tier_l3, ps.tier_l5, ps.l3_clean, ps.l5_clean,
             json.dumps(ps.b2b_arms),
             json.dumps([f"{n}({h}):{','.join(f)}" for n, h, f in ps.tired_arms]),
             json.dumps([f"{n}:{c}" for n, c in ps.excluded_arms]),
             1 if ps.tag else 0, now),
        )
    con.commit()
    con.close()


# ---------------------------------------------------------------- report
def report(states: dict[str, PenState]) -> str:
    lines = ["PEN-EDGE REPORT (shadow — annotates only)", "=" * 60]
    ranked = sorted(states.values(), key=lambda p: -p.edge_score)
    for ps in ranked:
        tag = ps.tag or "—"
        lines.append(
            f"{ps.team:<4} vs {ps.opponent:<4} score={ps.edge_score:<3} "
            f"L3={ps.l3_clean}({ps.tier_l3}) L5={ps.l5_clean}({ps.tier_l5}) "
            f"b2b={len(ps.b2b_arms)} tired={len(ps.tired_arms)}  {tag}"
        )
        if ps.excluded_arms:
            lines.append(f"     de-noised out: "
                         f"{', '.join(f'{n}({c})' for n, c in ps.excluded_arms)}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "raw/bullpen_usage.json"
    states = analyze(load_bullpen(path))
    print(report(states))
    # Integration: call from run_morning after board build:
    #   states = relievers.analyze(relievers.load_bullpen(path))
    #   relievers.write_ledger(DB_PATH, slate_date, states)
    #   annotate dashboard: for each bat, if states[opposing_pen].tag:
    #       append '|PEN-EDGE' to the bat's flag column (display only)
