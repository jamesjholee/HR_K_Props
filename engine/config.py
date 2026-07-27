"""Gate engine configuration.

Every threshold from the methodology lives here as a named constant.
When a rule changes after a graded slate, edit here and bump CONFIG_VERSION.
The results database tags every board with the config version that produced it.
"""

CONFIG_VERSION = "v1.5.1-2026-07-27"

# ---------------- Gate 2.5: last-3-starts pitcher form (SHADOW, v1.4) ----------------
# Annotates only. Season verdict is authoritative; flags never move probabilities.
FORM_STARTS = 3  # starts pulled from statsapi game log
FORM_MIN_BBE = 20  # below this recent sample -> N/A, no flag
FORM_HR_HOT = 2  # >=2 HR across the window = recent damage ("hot")
FORM_HH_HOT = 45.0  # or hard-hit% (EV>=95) at/above this
FORM_HH_CLEAN = 38.0  # 0 HR and HH% below this = recent "clean"
FORM_VELO_DROP = 1.0  # last-3 FF velo this many mph under season avg -> flag

# ---------------- Odds feed (/MLB/v2, five books, v1.4) ----------------
# Display + ledger only. Never feeds ranking. Sub-+400 auto-pass unchanged.
ODDS_BOOKS = ("draftkings", "fanduel", "betmgm", "prizepicks", "underdog")

# ---------------- Gate 2: pitcher vulnerability ----------------
PITCHER_BBE_FLOOR = (
    200  # below this: verdict capped at INSUFFICIENT (no confident fade)
)
PITCH_MIN_USAGE = 0.10  # pitch must be >=10% usage to count toward the vector
PITCH_MIN_BBE = 15  # per-pitch sample floor for a crushable verdict

# A pitch is "crushable" if it clears ANY damage trigger (with usage+BBE floors met)
PITCH_SLG_TRIGGER = 0.450
PITCH_ISO_TRIGGER = 0.180
PITCH_BARREL_TRIGGER = 0.10  # 10% barrel rate allowed on the pitch

BROAD_DAMAGE_MIN_PITCHES = 2  # 2+ crushable pitches => broad-damage TARGET
# exactly 1 crushable pitch => ONE-PITCH conditional

# Whiff overlay: downgraded per methodology — a MODIFIER, not a veto
ELITE_WHIFF_ARSENAL = 0.32  # usage-weighted whiff above this = one-notch caution flag
ELITE_K_PCT = 28.0  # aggregate K% flag (informational; logged, not gating)

# ---------------- Gate 3: batter composite ----------------
# Weights over normalized 0-1 axis scores; must sum to 1.0
W_BARREL = 0.30
W_PULLAIR = 0.20
W_BATSPEED = 0.15
W_ISO = 0.15
W_HRFB = 0.10
W_FB = 0.10

# Normalization caps (value at or above cap scores 1.0 on that axis)
CAP_BARREL = 18.0  # %
CAP_PULLAIR = 30.0  # %
BATSPEED_LO = 68.0  # mph -> 0.0
BATSPEED_HI = 77.0  # mph -> 1.0
CAP_ISO = 0.300
CAP_HRFB = 35.0  # %
CAP_FB = 45.0  # %

# Exception lanes (checked against the vector-filtered split)
SUBFLOOR_BARREL = 18.0  # barrel >= 18% vs cleared arm => include regardless of price
SUBFLOOR_BATSPEED = (
    75.0  # bat speed >= 75 vs cleared arm => include regardless of price
)
# both thresholds met => AUTO-PROMOTE

# Opportunity axis
MIN_PA_PER_GAME = 3.4  # below this = bench/platoon risk flag
BATTER_MIN_BBE = 12  # split-sample floor: below this, exceptions can't fire

# Shrinkage: composite regresses toward BASELINE_SCORE by split BBE
# effective = raw*(n/(n+K)) + baseline*(K/(n+K))
SHRINK_K = 20
BASELINE_SCORE = 35.0

# ---------------- L15 tiebreaker (never primary) ----------------
# v1.3: 5-condition highlight screen (ALL must pass for LOUD/promote)
L15_SCREEN_BARREL = 15.0  # barrel% >= 15
L15_SCREEN_HH = 38.0  # hard-hit% >= 38
L15_SCREEN_AIR = 55.0  # air% (100 - GB%) >= 55
L15_SCREEN_GB = 45.0  # GB% < 45
L15_SCREEN_SS = 25.0  # sweet-spot% >= 25
L15_NEAR_MISS = 4  # 4 of 5 => flag as near-miss dart, not full promote
L15_QUIET_SLG = 0.250  # demote flag threshold

# Screen B ("WARM", secondary priority — never outranks Screen A LOUD)
# Tightened v1.3: ISO and Barrel are MANDATORY (power core),
# plus any 2 of {HH, Air, Zone}. Original UI setting was "any 4 of 5",
# which could pass with zero barrels — closed that leak.
L15_B_ISO = 0.250  # mandatory
L15_B_BARREL = 10.0  # mandatory
L15_B_HH = 45.0  # any 2 of these 3
L15_B_AIR = 40.0
L15_B_ZONE = 18.0  # Zone metric not in hr-matchup API response;
# until source identified, require both of the
# 2 available (HH, Air)

# ---------------- Board assembly ----------------
BOARD_MIN_NAMES_PER_TARGET = 5  # broad-damage arms get wide coverage
BOARD_MAX_NAMES_PER_TARGET = 6
ONEPITCH_MAX_NAMES = 3  # one-pitch arms: only bats with damage on that pitch
TIER1_ODDS_FLOOR = 400  # sub-+400 goes to watchlist lane unless exception fires

# ---------------- Trajectory (tri-window L5/L10/L15) ----------------
TRAJ_MARGIN = 8.0  # heat-score delta (0-100 scale) to call HEATING/COOLING
# wide on purpose: L5 = 5 batted balls, direction only
