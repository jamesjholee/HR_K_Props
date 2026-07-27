"""Gate logic: pitcher vulnerability (Gate 2), batter composite (Gate 3),
L15 tiebreaker flags. Gate 4 (vector crossing) is performed server-side by the
hr-matchup selected-pitch filter; this module decides WHICH pitches go in that
filter (the vector) and scores what comes back."""

from . import config as C


# ---------------- Gate 2 ----------------

def gate2_pitcher(splits, pitch_types):
    """Returns verdict dict: TARGET / ONE-PITCH / FADE / INSUFFICIENT + vector."""
    season = splits.get("Season", {})
    bbe = season.get("bbe", 0)

    crushable = []
    considered = []
    for p in pitch_types:
        if p["usage"] < C.PITCH_MIN_USAGE:
            continue
        considered.append(p)
        if p["bbe"] < C.PITCH_MIN_BBE:
            continue
        triggers = []
        if p["slg"] >= C.PITCH_SLG_TRIGGER:
            triggers.append(f"SLG {p['slg']:.3f}")
        if p["iso"] >= C.PITCH_ISO_TRIGGER:
            triggers.append(f"ISO {p['iso']:.3f}")
        if p["barrel_pct"] >= C.PITCH_BARREL_TRIGGER:
            triggers.append(f"BRL {p['barrel_pct']*100:.1f}%")
        if triggers:
            crushable.append({**p, "triggers": triggers})

    # usage-weighted arsenal whiff (over considered pitches)
    tot_use = sum(p["usage"] for p in considered) or 1.0
    uw_whiff = sum(p["usage"] * p["whiff_pct"] for p in considered) / tot_use

    flags = []
    if uw_whiff >= C.ELITE_WHIFF_ARSENAL:
        flags.append(f"elite arsenal whiff {uw_whiff*100:.1f}% (caution modifier)")
    if season.get("k_pct", 0) * 100 >= C.ELITE_K_PCT:
        flags.append(f"high K% {season.get('k_pct',0)*100:.1f} (logged)")

    if len(crushable) >= C.BROAD_DAMAGE_MIN_PITCHES:
        verdict = "TARGET"
        note = f"broad damage: {len(crushable)} crushable pitches"
        if bbe < C.PITCHER_BBE_FLOOR:
            verdict = "TARGET-THIN"
            note += f" (thin sample: {bbe} BBE — log as thin-target category)"
    elif len(crushable) == 1:
        verdict = "ONE-PITCH" if bbe >= C.PITCHER_BBE_FLOOR else "ONE-PITCH-THIN"
        note = f"damage concentrated on {crushable[0]['name']}"
    elif bbe < C.PITCHER_BBE_FLOOR:
        verdict = "NO-READ"
        note = f"no crushable pitch, but {bbe} BBE < {C.PITCHER_BBE_FLOOR} — too thin to fade confidently"
    else:
        verdict = "FADE"
        note = "no crushable pitch clears triggers"

    # whiff overlay: modifier — TARGET with elite whiff drops to ONE-PITCH-tier caution
    if verdict == "TARGET" and uw_whiff >= C.ELITE_WHIFF_ARSENAL:
        note += "; whiff overlay applied (treat as conditional)"

    vector = [p["code"] for p in crushable]
    return {
        "verdict": verdict, "note": note, "bbe": bbe,
        "uw_whiff": uw_whiff, "flags": flags,
        "vector": vector,
        "crushable": crushable,
    }


# ---------------- Gate 3 ----------------

def _axis(val, cap):
    return max(0.0, min(1.0, val / cap)) if cap else 0.0


def _batspeed_axis(v):
    span = C.BATSPEED_HI - C.BATSPEED_LO
    return max(0.0, min(1.0, (v - C.BATSPEED_LO) / span))


def gate3_batter(b):
    """Composite score 0-100 over the vector-filtered split, plus exception lanes."""
    score = 100.0 * (
        C.W_BARREL * _axis(b["barrel_pct"], C.CAP_BARREL)
        + C.W_PULLAIR * _axis(b["pullair_pct"], C.CAP_PULLAIR)
        + C.W_BATSPEED * _batspeed_axis(b["bat_speed"])
        + C.W_ISO * _axis(b["iso"], C.CAP_ISO)
        + C.W_HRFB * _axis(b["hrfb_pct"], C.CAP_HRFB)
        + C.W_FB * _axis(b["fb_pct"], C.CAP_FB)
    )
    # shrinkage: thin vector-filtered splits regress toward baseline score
    n = b["bbe"]
    k = C.SHRINK_K
    score = score * (n / (n + k)) + C.BASELINE_SCORE * (k / (n + k))

    flags = []
    sample_ok = n >= C.BATTER_MIN_BBE
    sub_barrel = sample_ok and b["barrel_pct"] >= C.SUBFLOOR_BARREL
    sub_speed = sample_ok and b["bat_speed"] >= C.SUBFLOOR_BATSPEED
    if sub_barrel and sub_speed:
        flags.append("AUTO-PROMOTE (double exception)")
    elif sub_barrel:
        flags.append("sub-floor elite: barrel")
    elif sub_speed:
        flags.append("sub-floor elite: bat speed")
    if b["pa_per_game"] < C.MIN_PA_PER_GAME:
        flags.append("opportunity risk (low PA/G)")
    if b["bbe"] < C.BATTER_MIN_BBE:
        flags.append(f"thin split ({b['bbe']} BBE) — provisional")
    return {"score": round(score, 1), "flags": flags,
            "auto_promote": sub_barrel and sub_speed,
            "sub_floor_elite": sub_barrel or sub_speed}


# ---------------- L15 tiebreaker ----------------

def l15_flag(b15):
    """Returns 'LOUD', 'QUIET', or None. Tiebreaker only — never feeds the composite."""
    if b15 is None:
        return None
    gb = b15.get("gb_pct", 100.0)
    passes = sum([
        b15["barrel_pct"] >= C.L15_SCREEN_BARREL,
        b15.get("hh_pct", 0.0) >= C.L15_SCREEN_HH,
        (100.0 - gb) >= C.L15_SCREEN_AIR,
        gb < C.L15_SCREEN_GB,
        b15.get("sweetspot_pct", 0.0) >= C.L15_SCREEN_SS,
    ])
    if passes == 5:
        return "LOUD"
    if passes >= C.L15_NEAR_MISS:
        return "NEAR"
    # Screen B "WARM": ISO + Barrel mandatory (power core), plus HH and Air
    # (Zone condition parked — not in API; both available conditions required)
    if (b15.get("iso", 0.0) >= C.L15_B_ISO
            and b15["barrel_pct"] >= C.L15_B_BARREL
            and b15.get("hh_pct", 0.0) >= C.L15_B_HH
            and (100.0 - gb) >= C.L15_B_AIR):
        return "WARM"
    if b15["slg"] <= C.L15_QUIET_SLG and b15["hr"] == 0:
        return "QUIET"
    return None


def _heat(b):
    """Compact contact-quality score for one window: barrel + HH + air, 0-100."""
    if not b:
        return None
    air = 100.0 - b.get("gb_pct", 100.0)
    return (2.0 * b["barrel_pct"] + b.get("hh_pct", 0.0) + air) / 4.0


def trajectory(b15, b10, b5):
    """Direction of form across windows. Tiebreaker only, never primary.

    HEATING  = most recent 5 BBE clearly louder than the 15-window
    COOLING  = clearly quieter
    SUSTAINED = Screen-A LOUD in all three windows (strongest promote signal)
    L5 is 5 batted balls — direction only; margins are wide on purpose.
    """
    h15, h10, h5 = _heat(b15), _heat(b10), _heat(b5)
    if h15 is None or h5 is None:
        return None
    from engine.gates import l15_flag as _f  # reuse Screen A on each window
    if all(_f(w) == "LOUD" for w in (b15, b10, b5) if w) and b10 and b5:
        return "SUSTAINED"
    margin = C.TRAJ_MARGIN
    if h5 - h15 >= margin and (h10 is None or h10 >= h15 - 2):
        return "HEATING"
    if h15 - h5 >= margin:
        return "COOLING"
    return None
