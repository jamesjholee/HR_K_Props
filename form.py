"""form.py — Gate 2.5: last-3-starts pitcher form check (SHADOW ANNOTATOR).

Every starter gets graded twice: season data (Gate 2, unchanged, authoritative)
and last-3-starts hit data from PropFinder's /MLB/hit-data endpoint. Comparing
the two produces a FORM FLAG that ANNOTATES the tier sheet — it never
overwrites a season verdict and never moves hr_prob().

Flags:
  CONFIRMED         season target + recent damage too. Highest confidence.
  DECLINING-TARGET  season says crushed, last 3 starts look clean. Verdict
                    stands; caution prints; sizing is the human's call.
  EMERGING          season fade/no-read, but last 3 starts getting hit hard.
                    Own lane, smallest units only if ever acted on.
  (+VELO-DROP)      appended when last-3 four-seam velo sits >= FORM_VELO_DROP
                    mph below the season average (needs velo in hit-data).
  N/A               endpoint dark / schema surprise / thin sample. Never fatal.

DATA-HONESTY NOTE (v1.4 first ship): /MLB/hit-data was verified in a
LOGGED-IN browser on 2026-07-25 and its exact JSON schema + auth behavior are
unconfirmed. Everything below therefore:
  1. saves every raw response to raw/ (audit trail + schema discovery),
  2. hunts for fields across candidate key names instead of assuming one,
  3. degrades to N/A with a logged alert instead of crashing the morning run.
After the first live run, tighten _pick candidates to the real keys.

Start dates come from MLB statsapi game logs (known-good, unauthenticated).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import puller
from engine import config as C


# ---------- tiny defensive helpers ----------

def _pick(d, *names, default=None):
    """First present, non-None value among candidate key names."""
    for n in names:
        if isinstance(d, dict) and d.get(n) is not None:
            return d[n]
    return default


def _rows(raw):
    """hit-data may return a bare list or wrap rows under a key. Find the list."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for k in ("hits", "data", "rows", "results", "items", "hitData"):
            v = raw.get(k)
            if isinstance(v, list):
                return v
        # fall back: first list value found
        for v in raw.values():
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v
    return []


# ---------- start-date discovery (statsapi, known-good) ----------

def last_start_dates(pid, season, n=None, offline=False):
    n = n or C.FORM_STARTS
    raw = puller.mlb_gamelog(pid, season, offline)
    if not raw:
        return []
    dates = []
    for s in (raw.get("stats") or [{}])[0].get("splits", []):
        stat = s.get("stat") or {}
        if int(stat.get("gamesStarted") or 0) >= 1 and s.get("date"):
            dates.append(s["date"])
    return sorted(dates)[-n:]


# ---------- hit-data pull + summarize ----------

def _summarize(rows, pitcher_id=None):
    """Reduce raw hit rows -> {bbe, hr, hh_pct, ff_velo, air_pct, has_ev}.

    Vocab confirmed by /hit-data/filter-options (2026-07-25):
      results: snake_case ('home_run', 'field_out', 'hit_into_play', ...)
      trajectories: 'fly_ball' | 'ground_ball' | 'line_drive' | 'popup' | ...
      pitchTypes: FULL NAMES ('Four-seam FB', 'Sinker', 'Fastball', ...)
    EV / pitch velo are NOT filterable; whether rows carry them is unknown —
    both stay optional and the flags degrade without them."""
    bbe = hr = hard = ev_n = air = gb = 0
    ff_velo_sum = ff_velo_n = 0.0
    for r in rows:
        # if the endpoint ignored our pitcher filter, filter client-side
        rpid = _pick(r, "pitcherId", "pitcher_id", "pitcherID")
        if pitcher_id and rpid is not None and int(rpid) != int(pitcher_id):
            continue
        bbe += 1
        res = str(_pick(r, "result", "event", "playResult", "outcome",
                        default="")).lower()
        traj = str(_pick(r, "trajectory", "hitTrajectory", default="")).lower()
        if "home" in res or res == "hr":       # matches 'home_run'
            hr += 1
        if traj in ("fly_ball", "line_drive", "popup"):
            air += 1
        elif traj in ("ground_ball", "bunt_grounder"):
            gb += 1
        ev = _pick(r, "ev", "exitVelocity", "launchSpeed", "hitSpeed", "exitVelo")
        if ev is not None:
            ev_n += 1
            if float(ev) >= 95.0:
                hard += 1
        velo = _pick(r, "pitchVelo", "releaseSpeed", "pitchSpeed", "velo",
                     "startSpeed")
        ptype = str(_pick(r, "pitchType", "pitchCode", "pitch", default="")).lower()
        if velo is not None and ("four" in ptype or ptype in ("ff", "fa", "fastball")):
            ff_velo_sum += float(velo)
            ff_velo_n += 1
    tracked = air + gb
    return {
        "bbe": bbe, "hr": hr,
        "hh_pct": round(100.0 * hard / ev_n, 1) if ev_n else None,
        "air_pct": round(100.0 * air / tracked, 1) if tracked else None,
        "ff_velo": round(ff_velo_sum / ff_velo_n, 1) if ff_velo_n else None,
        "has_ev": ev_n > 0,
    }


def _season_ff_velo(pitch_types):
    for p in pitch_types or []:
        if p.get("code") in ("FF", "FA") and p.get("velo"):
            return float(p["velo"])
    return None


def _filter_dates(rows, dates):
    """Scope a full hit log to specific game dates (client-side, like the UI).
    Returns (matching_rows, undated_count) — undated rows can't be scoped."""
    ds = {str(d)[:10] for d in dates}
    out, undated = [], 0
    for r in rows:
        d = _pick(r, "date", "gameDate", "gameDateUtc", "gameTimeUtc", "gameDay")
        if d is None:
            undated += 1
            continue
        if str(d)[:10] in ds:
            out.append(r)
    return out, undated


# ---------- the gate ----------

def gate25(pid, season, g2, pitch_types, offline=False):
    """Return {'flag','detail','last3':{...}} — annotation only, never a verdict."""
    try:
        dates = last_start_dates(pid, season, offline=offline)
        if len(dates) < 2:
            return {"flag": "N/A", "detail": "fewer than 2 starts on log", "last3": {}}
        raw = puller.hit_data(pid, dates, offline, group="pitching")
        if raw is None:
            return {"flag": "N/A",
                    "detail": "hit-data endpoint dark (auth? offline fixture missing?)",
                    "last3": {}}
        all_rows = _rows(raw)
        rows, undated = _filter_dates(all_rows, dates)
        if not rows and all_rows and undated == len(all_rows):
            return {"flag": "N/A",
                    "detail": ("hit-data rows carry no recognizable date field — "
                               "paste one raw row so the parser can learn it"),
                    "last3": {}}
        s = _summarize(rows, pitcher_id=pid)
        if s["bbe"] < C.FORM_MIN_BBE:
            return {"flag": "N/A",
                    "detail": f"thin recent sample ({s['bbe']} BBE < {C.FORM_MIN_BBE})",
                    "last3": s}

        per_start_hr = s["hr"] / max(1, len(dates))
        hot = (s["hr"] >= C.FORM_HR_HOT
               or (s["hh_pct"] is not None and s["hh_pct"] >= C.FORM_HH_HOT))
        clean = (s["hr"] == 0
                 and (s["hh_pct"] is None or s["hh_pct"] < C.FORM_HH_CLEAN))

        v = g2["verdict"]
        if v.startswith("TARGET"):
            flag = "CONFIRMED" if hot else (
                "DECLINING-TARGET" if clean else "CONFIRMED?")
        elif v.startswith(("FADE", "NO-READ")):
            flag = "EMERGING" if hot else ""
        else:  # ONE-PITCH lanes: only annotate the loud direction
            flag = "CONFIRMED" if hot else ""

        # velo overlay (append, never replace)
        sv = _season_ff_velo(pitch_types)
        if sv and s["ff_velo"] and (sv - s["ff_velo"]) >= C.FORM_VELO_DROP:
            flag = (flag + "+" if flag else "") + \
                f"VELO-DROP({s['ff_velo']} vs {sv})"

        detail = (f"last {len(dates)} starts: {s['bbe']} BBE, {s['hr']} HR"
                  + (f", HH {s['hh_pct']}%" if s["hh_pct"] is not None else "")
                  + (f", air {s['air_pct']}%" if s.get("air_pct") is not None else "")
                  + (f", FF {s['ff_velo']}" if s["ff_velo"] else "")
                  + f" ({per_start_hr:.1f} HR/start)")
        return {"flag": flag or "—", "detail": detail, "last3": s}
    except Exception as e:  # Gate 2.5 must NEVER take down the morning run
        return {"flag": "N/A", "detail": f"gate25 error: {e}", "last3": {}}
