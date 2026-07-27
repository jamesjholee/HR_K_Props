"""profiles.py — DTP batter profiles (SHADOW MODE: tagged + logged, zero ranking effect).

Classifies every boarded bat's L15 window against the four DTP profiles using
the thresholds from the 2026-07-24 screenshots. Tags ride along into the DB
(inside the l15_flag column, pipe-separated) and print as a dashboard column.

RULES (v1.4, non-negotiable):
  - NOTHING here touches hr_prob(), the composite, promotes, or ranking.
  - After 5-6 graded slates, run_grade queries answer "do INSANE/ELITE tags
    out-hit untagged bats?" — whichever profile proves predictive earns weight
    WITH receipts, via a config change + version bump. Not before.

Inputs are the L15 hr-matchup batter dicts (percent-number scale, same as the
L15 screens). blast_pct and ld_pct are new fields carried by parsers v1.4.
"""

# ---- thresholds (screenshot spec, 2026-07-24 session) ----
INSANE = dict(ev=92, brl=20, pb=10, hh=50, bl=20, bs=75, fb=40)
ELITE = dict(ev=91, brl=15, pb=7, hh=45, bl=15, bs=72, ss=35)
FLYBALL = dict(ev=90, brl=12, hh=45, air=45, fb=35, pa=20, gb=40)
LINEDRIVE = dict(ev=91, brl=12, hh=50, ld=28, ss=35, bl=15, gb=35)

EMOJI = {"INSANE": "\U0001F4A3", "ELITE": "\U0001F680",
         "FLYBALL": "\U0001F340", "LINEDRIVE": "\U0001F3AF"}


def classify(b):
    """Return 'INSANE' | 'ELITE' | 'FLYBALL' | 'LINEDRIVE' | '' for one L15 window."""
    if not b:
        return ""
    ev, brl = b.get("avg_ev", 0), b.get("barrel_pct", 0)
    pb, hh = b.get("pullbarrel_pct", 0), b.get("hh_pct", 0)
    bl, bs = b.get("blast_pct", 0), b.get("bat_speed", 0)
    fb, gb = b.get("fb_pct", 0), b.get("gb_pct", 100)
    ss, ld = b.get("sweetspot_pct", 0), b.get("ld_pct", 0)
    air, pa = 100 - gb, b.get("pullair_pct", 0)

    t = INSANE
    if (ev >= t["ev"] and brl >= t["brl"] and pb >= t["pb"] and hh >= t["hh"]
            and bl >= t["bl"] and bs >= t["bs"] and fb >= t["fb"]):
        return "INSANE"
    t = ELITE
    if (ev >= t["ev"] and brl >= t["brl"] and pb >= t["pb"] and hh >= t["hh"]
            and bl >= t["bl"] and bs >= t["bs"] and ss >= t["ss"]):
        return "ELITE"
    t = FLYBALL
    if (ev >= t["ev"] and brl >= t["brl"] and hh >= t["hh"] and air >= t["air"]
            and fb >= t["fb"] and pa >= t["pa"] and gb <= t["gb"]):
        return "FLYBALL"
    t = LINEDRIVE
    if (ev >= t["ev"] and brl >= t["brl"] and hh >= t["hh"] and ld >= t["ld"]
            and ss >= t["ss"] and bl >= t["bl"] and gb <= t["gb"]):
        return "LINEDRIVE"
    return ""


def pretty(tag):
    return f"{EMOJI[tag]} {tag}" if tag in EMOJI else ""
