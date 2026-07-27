"""kmodel.py — v0 strikeout projections. PAPER TRACKING ONLY until calibrated.

Same data, opposite tail: arsenal whiff (usage-weighted, from Gate 2 input)
x opposing lineup K-proneness (team K rank splits from upcoming-games)
x workload estimate. Predictions lock at generation time (no-peek rule).

Heuristics documented inline; every constant is a named tunable.
"""

# ---- tunables (v0 heuristics — recalibrate after 12 paper slates) ----
BASE_BF = 24.0            # batters faced on a normal ~90 pitch leash
K_FROM_WHIFF = 0.82       # per-PA K prob ~= arsenal_whiff * this (v0 map)
TEAM_RANK_SWING = 0.22    # +/- proportional swing from team K rank (1..30)
SHELLED_NOTE_SLG = 0.500  # informational only

def team_k_factor(rank):
    """rank 1 = most strikeout-prone (most Ks) -> boost; 30 = hardest to K."""
    if not rank:
        return 1.0
    # linear: rank 1 -> 1+swing, rank 30 -> 1-swing, rank 15.5 -> 1.0
    return 1.0 + TEAM_RANK_SWING * (15.5 - rank) / 14.5

def pick_team_rank(rankings, pitcher_hand):
    """Prefer the vs-hand split, fall back to season."""
    want = "vs LHP" if pitcher_hand == "LHP" else "vs RHP"
    season = None
    for r in rankings or []:
        if r.get("split") == want:
            return r.get("rank")
        if r.get("split") == "Season":
            season = r.get("rank")
    return season

def project_k(arsenal_whiff, opp_rankings, pitcher_hand="RHP",
              est_bf=BASE_BF, note=""):
    """Return dict with projected K, range, and inputs for the log."""
    w = max(0.10, min(0.40, arsenal_whiff))       # clamp silly inputs
    per_pa = w * K_FROM_WHIFF
    rank = pick_team_rank(opp_rankings, pitcher_hand)
    factor = team_k_factor(rank)
    exp_k = est_bf * per_pa * factor
    return {
        "proj_k": round(exp_k, 1),
        "range": (max(0, round(exp_k - 1.5)), round(exp_k + 1.5)),
        "arsenal_whiff": round(w * 100, 1),
        "opp_k_rank": rank,
        "team_factor": round(factor, 3),
        "est_bf": est_bf,
        "note": note,
        "paper_only": True,
    }
