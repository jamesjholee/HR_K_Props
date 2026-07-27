"""Board assembly: runs gates over one game's data and emits the ranked
candidate pool in the standard table format. Flat units, wide board,
L15 as promote/demote only."""

from . import config as C
from .gates import gate2_pitcher, gate3_batter, l15_flag


def run_game(splits, pitch_types, matchup, matchup_l15=None):
    pitcher, arsenal, batters = matchup
    g2 = gate2_pitcher(splits, pitch_types) if pitch_types else None

    l15_by_id = {}
    if matchup_l15:
        _, _, b15s = matchup_l15
        l15_by_id = {b["id"]: b for b in b15s}

    rows = []
    for b in batters:
        g3 = gate3_batter(b)
        fl15 = l15_flag(l15_by_id.get(b["id"]))
        rows.append({**b, **g3, "l15": fl15})

    # rank: auto-promote first, then composite score; LOUD L15 breaks ties upward
    def sort_key(r):
        return (
            0 if r["auto_promote"] else 1,
            -r["score"],
            0 if r["l15"] == "LOUD" else (2 if r["l15"] == "QUIET" else 1),
        )
    rows.sort(key=sort_key)

    # board width by pitcher verdict
    if g2 is None or g2["verdict"] in ("INSUFFICIENT", "NO-READ"):
        width = C.BOARD_MIN_NAMES_PER_TARGET   # provisional pool, human decides
    elif g2["verdict"].startswith("TARGET"):
        width = C.BOARD_MAX_NAMES_PER_TARGET
    elif g2["verdict"].startswith("ONE-PITCH"):
        width = C.ONEPITCH_MAX_NAMES
    else:
        width = 0

    board = rows[:width]
    watch = [r for r in rows[width:] if r["sub_floor_elite"] or r["l15"] == "LOUD"]
    return {"pitcher": pitcher, "gate2": g2, "board": board, "watch": watch,
            "all_rows": rows, "config": C.CONFIG_VERSION}


def render(result):
    p = result["pitcher"]
    g2 = result["gate2"]
    lines = []
    lines.append(f"PITCHER: {p['name']} ({p['hand']})  [config {result['config']}]")
    if g2:
        lines.append(f"  Gate 2: {g2['verdict']} — {g2['note']}  (BBE {g2['bbe']}, "
                     f"arsenal whiff {g2['uw_whiff']*100:.1f}%)")
        if g2["vector"]:
            lines.append(f"  Vector: {', '.join(g2['vector'])}")
        for f in g2["flags"]:
            lines.append(f"  Flag: {f}")
    else:
        lines.append("  Gate 2: (pitch-type data not provided — verdict pending)")
    lines.append("")
    hdr = f"  {'#':<3}{'Bat':<20}{'Pos':<5}{'Score':<7}{'BRL%':<7}{'PAir%':<7}{'BatSpd':<8}{'ISO':<7}{'BBE':<5}{'L15':<7}Flags"
    lines.append("  BOARD (flat units)")
    lines.append(hdr)
    for i, r in enumerate(result["board"], 1):
        lines.append(
            f"  {i:<3}{r['name']:<20}{r['pos']:<5}{r['score']:<7}{r['barrel_pct']:<7}"
            f"{r['pullair_pct']:<7}{r['bat_speed']:<8}{r['iso']:<7.3f}{r['bbe']:<5}"
            f"{(r['l15'] or '-'):<7}{'; '.join(r['flags']) or '-'}"
        )
    if result["watch"]:
        lines.append("  WATCH / EXCEPTION LANE")
        for r in result["watch"]:
            lines.append(
                f"     {r['name']:<20}{r['pos']:<5}{r['score']:<7}{r['barrel_pct']:<7}"
                f"{r['pullair_pct']:<7}{r['bat_speed']:<8}{r['iso']:<7.3f}{r['bbe']:<5}"
                f"{(r['l15'] or '-'):<7}{'; '.join(r['flags']) or '-'}"
            )
    return "\n".join(lines)
