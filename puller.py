"""puller.py — PropFinder data pulls with cache-busting + freshness assertions.

Failure modes this module exists to prevent (documented 2026-07-23, slate #1):
  1. Stale cached responses on repeat pulls of the same URL
  2. Silent empty-lineup fields near lock
  3. Starter ID mismatches between sources (the 5/27 rule)

Offline mode: --offline reads JSON from ./raw/ instead of the network
(same files the network mode writes), so the pipeline can run on pasted data.
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

BASE = "https://api.propfinder.app/mlb"
ROOT = (
    "https://api.propfinder.app"  # v1.4: /MLB/v2, /MLB/park-factors etc. live off root
)
MLB_SCHED = "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}&hydrate=probablePitcher"
MLB_GAMELOG = (
    "https://statsapi.mlb.com/api/v1/people/{pid}/stats"
    "?stats=gameLog&group=pitching&season={season}"
)
RAW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw")

HEADERS = {
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Accept": "application/json, text/plain, */*",
    # Browser-parity headers (from the 2026-07-25 cURL capture):
    # some PropFinder endpoints appear to return empty arrays rather
    # than errors when Origin/Referer are absent, so send them always.
    "Origin": "https://propfinder.app",
    "Referer": "https://propfinder.app/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0.0.0 Safari/537.36"
    ),
}

# Optional auth: put your logged-in PropFinder cookie in cookie.txt (same
# folder as this file) and account-gated endpoints (/MLB/v2 odds,
# HighlightConfigs) light up. To get it: DevTools -> Network -> click any
# api.propfinder.app request while logged in -> Request Headers -> copy the
# full 'cookie:' value into the file. NEVER commit or share cookie.txt.
COOKIE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookie.txt")


def _headers():
    h = dict(HEADERS)
    try:
        if os.path.exists(COOKIE_PATH):
            v = open(COOKIE_PATH).read().strip()
            if v:
                h["Cookie"] = v
    except Exception:
        pass
    return h


def _bust(url):
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}_t={int(time.time())}"


def _save(name, data):
    os.makedirs(RAW_DIR, exist_ok=True)
    path = os.path.join(RAW_DIR, f"{name}.json")
    blob = json.dumps(data, separators=(",", ":"))
    with open(path, "w") as f:
        f.write(blob)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def _load(name):
    path = os.path.join(RAW_DIR, f"{name}.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def fetch(url, name, offline=False, required=True, tries=3):
    """Fetch with cache-busting; log timestamp+hash; offline reads ./raw/{name}.json.

    8/2: retry w/ backoff — a single PropFinder read-timeout killed the
    whole morning lock (run_morning:491, l5 window). 3 attempts, 5s/10s
    waits. On final failure: required pulls raise (no slate without
    upcoming-games); non-required pulls return None with a loud line —
    downstream already handles None (window skipped / game skipped with
    alert). One degraded game must never cost the other thirteen.
    """
    if offline:
        data = _load(name)
        if data is None:
            print(f"  [offline] MISSING fixture raw/{name}.json — skipping")
        return data
    import time

    import requests

    last = None
    for attempt in range(tries):
        try:
            r = requests.get(_bust(url), headers=_headers(), timeout=30)
            r.raise_for_status()
            data = r.json()
            break
        except Exception as e:
            last = e
            if attempt < tries - 1:
                wait = 5 * (attempt + 1)
                print(
                    f"  [retry] {name}: {type(e).__name__} — "
                    f"attempt {attempt + 2}/{tries} in {wait}s"
                )
                time.sleep(wait)
    else:
        if required:
            raise last
        print(
            f"  [degraded] {name}: {type(last).__name__} after {tries} tries "
            f"— continuing without it"
        )
        return None
    h = _save(name, data)
    print(
        f"  [pull] {name}  hash={h}  {datetime.now(timezone.utc).isoformat(timespec='seconds')}"
    )
    return data


# ---------------- endpoint wrappers ----------------


def upcoming_games(offline=False):
    return fetch(f"{BASE}/upcoming-games", "upcoming_games", offline)


def weather_notes(offline=False):
    return fetch(f"{BASE}/weather-notes", "weather_notes", offline)


def pitcher_splits(pid, season, offline=False):
    return fetch(
        f"{BASE}/pitcher-splits?pitcherId={pid}&season={season}",
        f"splits_{pid}",
        offline,
    )


def pitch_type_stats(pid, season, offline=False):
    return fetch(
        f"{BASE}/pitch-type-stats?playerId={pid}&season={season}"
        f"&playerType=pitcher&handedness=all",
        f"pt_{pid}",
        offline,
    )


def hr_matchup(
    pid, team_id, season, vs_rhb=None, vs_lhb=None, range_value=None, offline=False
):
    url = f"{BASE}/hr-matchup?pitcherId={pid}&teamId={team_id}&season={season}"
    tag = f"matchup_{pid}_{team_id}"
    if vs_rhb:
        url += "&selectedVsRHB=" + "%2C".join(vs_rhb)
    if vs_lhb:
        url += "&selectedVsLHB=" + "%2C".join(vs_lhb)
    if range_value:
        url += f"&rangeValue={range_value}&rangeType=battedBall"
        tag += f"_l{range_value}"
    if vs_rhb or vs_lhb:
        tag += "_vec"
    # 8/2: required=False — a failed matchup pull skips one window/game
    # (downstream None-handling + alert), never the whole slate
    return fetch(url, tag, offline, required=False)


def mlb_schedule(date_str, offline=False):
    return fetch(MLB_SCHED.format(date=date_str), f"mlb_sched_{date_str}", offline)


# ---------------- v1.4 endpoints (browser-mapped 2026-07-25) ----------------
# CAVEAT: verified with a logged-in cookie; unauthenticated behavior unknown.
# All wrapped in fetch_soft: any HTTP/parse failure returns None + prints,
# never takes down the run. probe_endpoints.py reports which work cookieless.


def fetch_soft(url, name, offline=False):
    try:
        return fetch(url, name, offline)
    except Exception as e:
        print(f"  [pull] {name} FAILED (soft): {e}")
        return None


def fetch_post(url, body, name, offline=False):
    """POST with JSON body (hit-data style endpoints). Same raw/ audit trail."""
    if offline:
        return _load(name)
    import requests

    r = requests.post(url, json=body, headers=_headers(), timeout=30)
    r.raise_for_status()
    data = r.json()
    h = _save(name, data)
    print(
        f"  [post] {name}  hash={h}  {datetime.now(timezone.utc).isoformat(timespec='seconds')}"
    )
    return data


def fetch_soft_post(url, body, name, offline=False):
    try:
        return fetch_post(url, body, name, offline)
    except Exception as e:
        print(f"  [post] {name} FAILED (soft): {e}")
        return None


def odds_v2(
    offline=False, books=("draftkings", "fanduel", "betmgm", "prizepicks", "underdog")
):
    q = "&".join(f"sportsbooks={b}" for b in books)
    return fetch_soft(f"{ROOT}/MLB/v2?{q}", "odds_v2", offline)


def hit_data(pid, dates=None, offline=False, group="pitching"):
    """GET /MLB/hit-data?playerId=X&group=pitching|hitting (captured 2026-07-25).

    The endpoint is PLAYER-SCOPED: it returns the player's full hit log and
    ALL filtering (dates, pitch types, parks...) happens client-side — the
    UI's filter menus never touch the server. So: one pull per pitcher with
    group=pitching (hits ALLOWED), then form.py scopes to the last-3-start
    dates itself. group=hitting = per-batter hit logs (future recency tool).
    `dates` is accepted for signature stability but filtering is the caller's."""
    return fetch_soft(
        f"{ROOT}/MLB/hit-data?playerId={pid}&group={group}", f"hitdata_{pid}", offline
    )


def zone_matchups_all(season, offline=False):
    """Slate-wide zone matchups (verified live 2026-07-25, no pitcher path)."""
    return fetch_soft(
        f"{BASE}/zone-matchups?season={season}&minPitchUsage=0",
        "zone_matchups_all",
        offline,
    )


def hit_data_filter_options(offline=False):
    return fetch_soft(f"{BASE}/hit-data/filter-options", "hitdata_filters", offline)


def mlb_gamelog(pid, season, offline=False):
    return fetch_soft(
        MLB_GAMELOG.format(pid=pid, season=season), f"gamelog_{pid}", offline
    )


def lineups(game_ids, offline=False):
    """Lightweight lineup poller (no weather bloat) — watcher upgrade, v1.4+."""
    ids = ",".join(str(g) for g in game_ids)
    return fetch_soft(f"{BASE}/lineups?gameIds={ids}", "lineups", offline)


def park_factors(bat_side, offline=False):
    return fetch_soft(
        f"{ROOT}/MLB/park-factors?batSide={bat_side}",
        f"park_factors_{bat_side}",
        offline,
    )


def hr_risk(date_str, offline=False):
    """PropFinder's own model output — logged nightly as a benchmark column."""
    return fetch_soft(
        f"{BASE}/hr-risk?dates={date_str}", f"hr_risk_{date_str}", offline
    )


def bullpen_usage(offline=False):
    """Reliever availability/usage feed (found 2026-07-25) — the #1 unbuilt
    gap's data source. Pulled + saved for schema discovery; the reliever
    framework itself ships in a later version, evidence-first as always."""
    return fetch_soft(f"{ROOT}/MLB/bullpen-usage", "bullpen_usage", offline)


# ---------------- freshness assertions ----------------


def check_freshness(games, now_utc=None):
    """Return list of alert strings. Empty lineups near lock, starter gaps."""
    alerts = []
    now = now_utc or datetime.now(timezone.utc)
    for g in games:
        gid = g.get("id")
        try:
            start = datetime.fromisoformat(g["gameDate"].replace("Z", "+00:00"))
        except Exception:
            continue
        mins = (start - now).total_seconds() / 60
        home = g.get("homeTeam", {}).get("code", "?")
        vis = g.get("visitorTeam", {}).get("code", "?")
        label = f"{vis}@{home} ({gid})"
        if not g.get("homePitcherId") or not g.get("visitorPitcherId"):
            alerts.append(
                f"STARTER MISSING: {label} — verify in MLB app (opener? scratch?)"
            )
        if 0 < mins <= 90:
            if not g.get("homeBattingOrder"):
                alerts.append(
                    f"LINEUP EMPTY T-{int(mins)}m: {label} home side — re-poll / check app"
                )
            if not g.get("visitorBattingOrder"):
                alerts.append(
                    f"LINEUP EMPTY T-{int(mins)}m: {label} visitor side — re-poll / check app"
                )
    return alerts


def crosscheck_starters(games, mlb_sched):
    """Compare PropFinder starter IDs vs MLB probables. Any mismatch = 5/27 alert."""
    alerts = []
    if not mlb_sched:
        return [
            "MLB schedule unavailable — starter cross-check SKIPPED, verify manually"
        ]
    # 7/29 DH fix: a team can have TWO legitimate probables on a
    # doubleheader day — the old {tid: pid} dict kept only the last one,
    # firing false STARTER MISMATCH alerts. Keep the full set per team.
    probables = {}
    for d in mlb_sched.get("dates", []):
        for g in d.get("games", []):
            for side in ("home", "away"):
                t = g["teams"][side]
                pid = (t.get("probablePitcher") or {}).get("id")
                if pid:
                    probables.setdefault(t["team"]["id"], set()).add(pid)
    for g in games:
        for side, key in (
            ("homeTeam", "homePitcherId"),
            ("visitorTeam", "visitorPitcherId"),
        ):
            tid = g.get(side, {}).get("id")
            pf = g.get(key)
            mlb = probables.get(tid)
            if pf and mlb and pf not in mlb:
                alerts.append(
                    f"STARTER MISMATCH {g[side].get('code')}: "
                    f"PropFinder={pf} vs MLB={'/'.join(str(x) for x in sorted(mlb))}"
                    f" — 5/27 RULE: verify before betting"
                )
    return alerts
