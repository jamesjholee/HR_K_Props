"""Parsers for PropFinder API JSON.

Endpoint quirks handled here so gate logic sees one clean schema:
  1. pitch-type-stats returns DECIMALS (0.167); splits + hr-matchup return
     PERCENT NUMBERS (16.7). Everything is normalized to decimals internally.
  2. Missing key == zero (no 'hr' field when 0 HR, no 'barrelPct' when 0 barrels).
  3. hr-matchup batter rows are platoon/vector-filtered splits — small samples
     by design; BBE is carried through so the gates can shrink/flag.
"""
import json


def g(d, key, default=0.0):
    """Safe get: missing key means zero in this API."""
    v = d.get(key, default)
    return default if v is None else v


def load_json(path):
    with open(path) as f:
        return json.load(f)


def parse_pitcher_splits(raw):
    """pitcher-splits endpoint -> {split_name: metrics}. Percent fields -> decimals."""
    out = {}
    for row in raw.get("rows", []):
        out[row["split"]] = {
            "ip": g(row, "ip"),
            "bf": g(row, "battersFaced"),
            "hr": g(row, "hr"),
            "hr9": g(row, "hrPer9"),
            "slg": g(row, "slg"),
            "iso": g(row, "iso"),
            "woba": g(row, "woba"),
            "k_pct": g(row, "kPct") / 100.0,
            "whiff_pct": g(row, "whiffPct") / 100.0,
            "barrel_pct": g(row, "barrelPct") / 100.0,
            "hrfb_pct": g(row, "hrFbPct") / 100.0,
            "pullair_pct": g(row, "pullAirPct") / 100.0,
            "fb_pct": g(row, "fbPct") / 100.0,
            "meatball_pct": g(row, "meatballPct") / 100.0,
            "avg_ev": g(row, "avgEV"),
            "bbe": int(g(row, "bbe")),
            "fip": g(row, "fip"),
        }
    return out


def parse_pitch_types(raw):
    """pitch-type-stats endpoint -> list of per-pitch dicts. Already decimals."""
    pitches = []
    for p in raw:
        pitches.append({
            "code": p["pitchCode"],
            "name": g(p, "pitchName", p["pitchCode"]),
            "usage": g(p, "percentage"),
            "velo": g(p, "avgVelo"),
            "slg": g(p, "slg"),
            "iso": g(p, "iso"),
            "woba": g(p, "wOBA"),
            "hr": int(g(p, "hr")),
            "bbe": int(g(p, "bbe")),
            "whiff_pct": g(p, "whiffPct"),
            "barrel_pct": g(p, "barrelPct"),
            "pullair_pct": g(p, "pullAirPct"),
            "gb_pct": g(p, "gbPct"),
            "fb_pct": g(p, "fbPct"),
            "avg_la": g(p, "avgLA"),
        })
    return pitches


def parse_hr_matchup(raw):
    """hr-matchup endpoint -> (pitcher_info, arsenal, batters). Percent -> decimals."""
    pit = raw["pitcher"]
    pitcher = {"id": pit["id"], "name": pit["name"], "hand": g(pit, "pitchingType", "?")}
    arsenal = {
        "vsRHB": [(a["code"], g(a, "pct") / 100.0) for a in raw.get("arsenalVsRHB", [])],
        "vsLHB": [(a["code"], g(a, "pct") / 100.0) for a in raw.get("arsenalVsLHB", [])],
    }
    batters = []
    for b in raw.get("batters", []):
        batters.append({
            "id": b["playerId"],
            "name": b["name"],
            "hand": g(b, "battingType", "?"),
            "pos": g(b, "positionAbbreviation", "?"),
            "pa": int(g(b, "pa")),
            "pa_per_game": g(b, "paPerGame"),
            "hr": int(g(b, "hr")),
            "near_hr": int(g(b, "nearHr")),
            "slg": g(b, "slg"),
            "iso": g(b, "iso"),
            "woba": g(b, "woba"),
            "k_pct": g(b, "kPct") / 100.0,
            "bb_pct": g(b, "bbPct") / 100.0,
            "barrel_pct": g(b, "barrelPct"),          # kept as % number for config compare
            "pullbarrel_pct": g(b, "pullBarrelPct"),
            "pullair_pct": g(b, "pullAirPct"),
            "fb_pct": g(b, "fbPct"),
            "hrfb_pct": g(b, "hrPerFbPct"),
            "bat_speed": g(b, "avgBatSpeed"),
            "fastswing_pct": g(b, "fastSwingPct"),
            "hh_pct": g(b, "hhPct"),                   # % numbers for L15 screens
            "gb_pct": g(b, "gbPct"),
            "sweetspot_pct": g(b, "sweetSpotPct"),
            "blast_pct": g(b, "blastPct"),             # v1.4: DTP profile inputs
            "ld_pct": g(b, "ldPct"),
            "whiff_pct": g(b, "whiffPct") / 100.0,
            "bbe": int(g(b, "bbe")),
            "dist300": int(g(b, "dist300Plus")),
            "dist350": int(g(b, "dist350Plus")),
            "avg_ev": g(b, "avgEV"),
        })
    return pitcher, arsenal, batters
