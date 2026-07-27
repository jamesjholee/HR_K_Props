# HR Engine — daily HR prop research app (v1.4)

## New in v1.4 (2026-07-25)
- `profiles.py` — DTP batter profiles (INSANE/ELITE/FLYBALL/LINEDRIVE), SHADOW
  MODE: tagged on the dashboard + logged in the DB (l15_flag column, `|P:TAG`),
  zero ranking effect. Grade after 5-6 slates before any profile earns weight.
- `form.py` — Gate 2.5: last-3-starts pitcher form via /mlb/hit-data + statsapi
  game logs. Flags CONFIRMED / DECLINING-TARGET / EMERGING / +VELO-DROP.
  ANNOTATES ONLY — season verdicts stay authoritative. Own DB table
  (pitcher_form). Degrades to N/A if the endpoint is dark.
- `odds.py` — /MLB/v2 five-book feed (DK/FD/MGM/PrizePicks/Underdog): best HR
  price + auto-EV column on the dashboard, locked to odds_lock table.
  Display/ledger only; human still verifies the live price at bet time.
- `probe_endpoints.py` — RUN THIS ONCE FIRST: tests all 7 browser-mapped
  endpoints without a cookie, saves fixtures to raw/. Anything DARK is
  cookie-gated; its feature stays defensive until we add the cookie or fix
  params. The /MLB/v2 and hit-data parsers are schema-hunting on purpose —
  attach the saved raw files to the next session so they can be locked down.
- New puller wrappers (lineups poller, park-factors, hr-risk) are shipped but
  NOT yet wired into the run — agenda items #4-6.
- DB migrates itself (CREATE TABLE IF NOT EXISTS): your existing hrapp.db is
  safe. Back it up anyway.
- OPTIONAL COOKIE: account-gated endpoints (/MLB/v2 odds, HighlightConfigs)
  return 401 without login. Create `cookie.txt` next to puller.py containing
  your logged-in PropFinder cookie (DevTools -> Network -> any
  api.propfinder.app request -> Request Headers -> copy the 'cookie:' value).
  The app attaches it automatically. Never share or commit this file; if it
  expires, odds simply go blank again — grab a fresh one.


Personal research tool. Not financial advice. Paper-tracks a K model.

## Setup (once)
```
python3 -m venv venv && source venv/bin/activate    # optional
pip install requests
```
That's the only dependency. SQLite is built into Python.

## Daily workflow
```
# Morning (~9am): pull slate, run gates, build boards, lock K sheet
python3 run_morning.py

# Anytime before lock: re-run — re-pulls lineups, re-fires alerts,
# applies scratches, refreshes L15 windows. Re-running is safe and encouraged.
python3 run_morning.py

# Open the dashboard (also works on your phone if you copy the file):
out/dashboard.html

# Evening: paste onlyhomers.com/daily rows into a file, then grade
python3 run_grade.py --date 2026-07-23 --hrfile todays_hrs.tsv --k 694297=6
```

## What the human still does (permanently)
1. Verify starters vs the MLB app when any ⚠ alert fires (the 5/27 rule).
2. Confirm lineups at T-30. Empty-lineup alerts mean CHECK THE APP.
3. Decide unit sizing. Flat units; exception lanes smallest; sub-+400 = pass.
4. Enter prop prices and compare to the breakeven column yourself.
5. All promote/demote overrides — log them; the DB has a column for it.

## Failure modes this app was built around (2026-07-23, slate #1)
- Stale cached API responses -> every request is cache-busted + hashed.
- Empty lineups near lock -> freshness assertions + loud alerts.
- Starter mismatches (Davis Martin/Sandlin; Waldron opener) -> MLB cross-check.
- No-peek rule: predictions lock with timestamps at generation. Never
  regenerate a prediction after seeing live data.

## Offline / paste fallback
If a pull fails, save the JSON your browser gets into `raw/` using the same
filenames the puller writes (see puller.py), then run with `--offline`.

## Files
- `run_morning.py`  orchestrator + dashboard renderer
- `run_grade.py`    evening grading (HR + paper-K)
- `puller.py`       endpoints, cache-busting, freshness, MLB cross-check
- `engine/`         gates v1.3 (Gate 2 vectors, composite, L15 screens A/B)
- `kmodel.py`       v0 K projections — PAPER ONLY until ~12 slates calibrated
- `db.py`           SQLite schema (verdicts, boards, k_paper, results, alerts)
- `hrapp.db`        the season record — back this up
- `raw/`            every pull, hashed + timestamped (audit trail)

## Tunables
All thresholds are named constants in `engine/config.py` (board widths,
pitch floors, screen thresholds) and the top of `kmodel.py`. Version-stamp
any change by editing CONFIG_VERSION so the DB can grade eras separately.

## Roadmap (from the carry-forward)
reliever/opener framework (#1 gap) · odds feed for auto-EV · zone fit
(endpoint unknown) · tri-window L5/L10/L15 · Claude API writeups (batch,
~$5/mo) · public static site + timestamped posting once edge is demonstrated
# mlbProps
