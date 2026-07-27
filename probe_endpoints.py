"""probe_endpoints.py — test the 7 browser-mapped endpoints WITHOUT a cookie.

Run once on your machine:  python3 probe_endpoints.py
Answers the 2026-07-25 caveat ("verified logged-in; test unauthenticated"),
saves every raw response into raw/ (real fixtures for tightening the v1.4
defensive parsers), and prints a schema peek per endpoint.

Anything that comes back 401/403/empty here needs the cookie plan
(local config header) before its feature can go live.
"""
import json, sys, os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import puller


def peek(name, data):
    if data is None:
        print(f"  {name:<22} DARK (error/auth/empty) — see message above")
        return
    kind = type(data).__name__
    if isinstance(data, dict):
        keys = list(data.keys())[:8]
        print(f"  {name:<22} OK  dict keys: {keys}")
    elif isinstance(data, list):
        head = data[0] if data else None
        hk = list(head.keys())[:8] if isinstance(head, dict) else head
        print(f"  {name:<22} OK  list[{len(data)}], row keys: {hk}")
    else:
        print(f"  {name:<22} OK  ({kind})")


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"=== PropFinder endpoint probe (cookieless) — {today} ===\n"
          "Every response saved to raw/ — attach the interesting ones to the "
          "next session so the parsers can be locked to the real schema.\n")

    # seed ids from the known-good endpoint
    games = puller.fetch_soft(f"{puller.BASE}/upcoming-games", "upcoming_games")
    gid = pid = None
    for g in games or []:
        gid = gid or g.get("id")
        pid = pid or g.get("homePitcherId") or g.get("visitorPitcherId")
        if gid and pid:
            break
    print(f"seed gameId={gid}  pitcherId={pid}\n")

    peek("odds /MLB/v2", puller.odds_v2())
    peek("hit-data filters", puller.hit_data_filter_options())
    peek("zone-matchups (all)", puller.zone_matchups_all(today[:4]))
    if pid:
        peek("hit-data (pitcher)", puller.hit_data(pid))
    if gid:
        peek("lineups poller", puller.lineups([gid]))
    peek("park-factors L", puller.park_factors("L"))
    peek("park-factors R", puller.park_factors("R"))
    peek("hr-risk", puller.hr_risk(today))
    peek("bullpen-usage", puller.bullpen_usage())
    peek("weather-notes", puller.weather_notes())
    # HighlightConfigs dropped: those are user-saved UI screens; ours live in
    # engine/config.py + profiles.py, versioned and graded locally.

    print("\nDone. DARK rows = cookie-gated — check cookie.txt sits next to "
          "puller.py in the folder you run from. hit-data is player-scoped "
          "(playerId+group) and confirmed working as of 2026-07-25.")


if __name__ == "__main__":
    main()
