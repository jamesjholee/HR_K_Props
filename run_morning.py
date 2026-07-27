"""run_morning.py — daily orchestrator.  (v1.5)

Usage:
  python3 run_morning.py                 # live pulls (your machine)
  python3 run_morning.py --offline       # read fixtures from ./raw/
  python3 run_morning.py --date 2026-07-24

Re-run anytime: closer to lock it re-checks lineups + freshness.
Vector matchup + L15 pulls happen automatically for cleared arms.
Output: out/dashboard.html + rows locked into hrapp.db (timestamped).

v1.5 changes (all marked with "# v1.5"):
  - relievers.py wired in: pen fatigue analysis, pen_edge ledger,
    PEN-EDGE bat annotation (SHADOW: display/ledger only).
  - odds.build_book called with slate date (new signature), with a
    v1.4 fallback + warning until odds.py is updated.
  - today_games now LOGS excluded games (instrumentation for the
    COL@MIL-class gap; filtering behavior unchanged).
  - 7/27 hotfix A: slate_date() — date filter compares in ET, not UTC
    (West Coast night games were rolling to the next UTC date and
    being dropped; verified live 7/27, 3 games recovered).
  - 7/27 hotfix B: pitcher_names() — resolve pitcher IDs to full names
    via the statsapi schedule already pulled for the starter
    cross-check. Falls back to "TEAM #id" when no probable is posted
    (correct behavior for unresolved starters, e.g. opener/scratch).
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import relievers

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db
import form
import kmodel
import odds
import profiles
import puller
from engine import config as C
from engine import gates, parsers
from engine.board import run_game

DB_FILE = "hrapp.db"  # v1.5: single place for the ledger path


def breakeven(p):
    return f"+{round((1.0 / p - 1.0) * 100)}" if p > 0 else "—"


def hr_prob(row, order=None):
    """v0 mapping: composite score -> calibrated-ish HR prob. Documented heuristic."""
    base = 0.12
    p = base * (1.0 + (row["score"] - 50.0) / 50.0 * 0.8)
    if row.get("auto_promote"):
        p += 0.02
    if row.get("l15") == "LOUD":
        p += 0.02
    elif row.get("l15") in ("NEAR", "WARM"):
        p += 0.01
    elif row.get("l15") == "QUIET":
        p -= 0.02
    if order and order <= 4:
        p += 0.01
    return max(0.08, min(0.25, round(p, 2)))


def slate_date(game_date_iso: str) -> str:
    """UTC ISO timestamp -> slate date in ET (MLB scheduling convention).

    7/27 hotfix A: no regular-season first pitch crosses midnight ET,
    so anchoring on America/New_York can never produce a false
    inclusion — but a UTC prefix match drops West Coast night games.
    """
    dt = datetime.fromisoformat(game_date_iso.replace("Z", "+00:00"))
    return dt.astimezone(ZoneInfo("America/New_York")).date().isoformat()


def today_games(all_games, date_str):
    out, excluded = [], []
    for g in all_games or []:
        gd = g.get("gameDate") or ""
        try:
            match = slate_date(gd) == date_str  # 7/27 hotfix A: ET compare
        except ValueError:
            match = False  # unparseable date -> excluded + logged, never crashes
        if match:
            out.append(g)
        else:
            # v1.5 instrumentation: make dropped games VISIBLE. After the ET
            # fix, anything logged here is a GENUINELY off-slate game.
            try:
                excluded.append(
                    f"{g['visitorTeam']['code']}@{g['homeTeam']['code']}"
                    f" (gameDate={gd or '?'})"
                )
            except (KeyError, TypeError):
                excluded.append(f"<unparseable game> (gameDate={gd or '?'})")
    return out, excluded


def pitcher_names(sched):
    """statsapi schedule -> {pitcher_id: fullName}.  (7/27 hotfix B)

    Uses the schedule pull that already exists for the starter
    cross-check; probablePitcher carries id + fullName per side.
    """
    out = {}
    for d in (sched or {}).get("dates", []):
        for g in d.get("games", []):
            for side in ("home", "away"):
                pp = (g.get("teams", {}).get(side, {}) or {}).get(
                    "probablePitcher"
                ) or {}
                if pp.get("id") and pp.get("fullName"):
                    out[int(pp["id"])] = pp["fullName"]
    return out


def order_map(g):
    """batting order strings -> {player_id: slot} for both sides."""
    m = {}
    for key in ("homeBattingOrder", "visitorBattingOrder"):
        ids = [x for x in (g.get(key) or "").split(",") if x.strip()]
        for i, pid in enumerate(ids, 1):
            m[int(pid)] = i
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = ap.parse_args()
    off = args.offline
    date = args.date

    print(
        f"=== HR Engine morning run — {date} — {C.CONFIG_VERSION} "
        f"{'[OFFLINE]' if off else '[LIVE]'} ==="
    )
    db.open_slate(date, C.CONFIG_VERSION)

    games_raw = puller.upcoming_games(off)
    games, excluded_games = today_games(games_raw, date)  # v1.5 signature
    if not games:
        print(
            "No games found for date — check raw/upcoming_games.json or the date arg."
        )
        return
    print(f"{len(games)} games on the slate.")
    # v1.5: excluded-game visibility (COL@MIL-class gap instrumentation)
    for ex in excluded_games:
        msg = f"game excluded by date filter: {ex}"
        print(f"  ℹ {msg}")
        db.log_alert(date, "info", msg)

    # ---- alerts: freshness + starter cross-check ----
    alerts = puller.check_freshness(games)
    sched = puller.mlb_schedule(date, off)
    alerts += puller.crosscheck_starters(games, sched)
    for a in alerts:
        print(f"  ⚠ {a}")
        db.log_alert(date, "warn", a)

    # 7/27 hotfix B: pitcher ID -> full name (statsapi probables)
    pname = pitcher_names(sched)

    season = int(date[:4])
    slate_rows, k_rows, form_rows = [], [], []

    # ---- v1.5: bullpen fatigue (SHADOW: annotates + own ledger, zero ranking effect) ----
    pen_states = {}
    try:
        pen_states = relievers.analyze(
            relievers.load_bullpen(os.path.join("raw", "bullpen_usage.json"))
        )
        relievers.write_ledger(DB_FILE, date, pen_states)
        tagged = sorted(ps.team for ps in pen_states.values() if ps.tag)
        if tagged:
            print(f"Pen-edge: {len(tagged)} tagged pens: {', '.join(tagged)}")
        else:
            print("Pen-edge: no pens over the floor today.")
    except FileNotFoundError:
        msg = "raw/bullpen_usage.json missing — PEN-EDGE blank this run"
        print(f"  ⚠ {msg}")
        db.log_alert(date, "info", msg)
    except Exception as e:  # never let the shadow lane kill the board
        msg = f"relievers.py failed ({e}) — PEN-EDGE blank this run"
        print(f"  ⚠ {msg}")
        db.log_alert(date, "warn", msg)

    # ---- v1.4/v1.5: odds feed, one pull per run (display/ledger only) ----
    # v1.5: new signature passes the slate date so the locked parser can
    # drop cross-date rows (feed mixes dates — verified 2026-07-27).
    try:
        book = odds.build_book(off, date)
    except TypeError:
        book = odds.build_book(off)  # v1.4 fallback
        msg = (
            "odds.py still v1.4 — cross-date/alt-line junk risk; "
            "update odds.py to the locked parser"
        )
        print(f"  ⚠ {msg}")
        db.log_alert(date, "warn", msg)
    if book["n"]:
        print(f"Odds feed: {book['n']} HR props parsed across books.")
    else:
        msg = (
            "odds feed empty/dark — EV column blank this run (prices manual, as before)"
        )
        print(f"  ⚠ {msg}")
        db.log_alert(date, "info", msg)

    for g in games:
        home, vis = g["homeTeam"], g["visitorTeam"]
        label = f"{vis['code']}@{home['code']}"
        omap = order_map(g)
        for key, own_team, opp_team in (
            ("homePitcherId", home, vis),
            ("visitorPitcherId", vis, home),
        ):
            pid = g.get(key)
            if not pid:
                continue
            pt_raw = puller.pitch_type_stats(pid, season, off)
            sp_raw = puller.pitcher_splits(pid, season, off)
            if not pt_raw or not sp_raw:
                db.log_alert(date, "warn", f"{label}: missing data for pitcher {pid}")
                continue
            pt = parsers.parse_pitch_types(pt_raw)
            sp = parsers.parse_pitcher_splits(sp_raw)
            g2 = gates.gate2_pitcher(sp, pt)
            # 7/27 hotfix B: resolve name; fall back to TEAM #id when no
            # probable is posted (unresolved starter should LOOK unresolved).
            name = pname.get(int(pid), f"{own_team['code']} #{pid}")
            vec = [p["code"] for p in g2.get("crushable", [])]
            db.log_verdict(
                date,
                pid,
                name,
                g2["verdict"],
                vec,
                g2["uw_whiff"],
                g2.get("bbe", 0),
                g2.get("note", ""),
            )
            print(
                f"\n{label} — {name} ({own_team['code']}): {g2['verdict']}"
                f"  vector={','.join(vec) or '—'}  whiff={g2['uw_whiff']:.1%}"
            )

            # v1.5: the pen a bat faces LATE is the same side as this starter
            pen = pen_states.get(own_team["code"])  # v1.5
            pen_tag = "|PEN-EDGE" if (pen and pen.tag) else ""  # v1.5

            # v1.4 Gate 2.5 — last-3-starts form (SHADOW: annotates, never overrides)
            fm = form.gate25(pid, season, g2, pt, off)
            db.log_form(date, pid, name, g2["verdict"], fm)
            form_rows.append((label, name, g2["verdict"], fm["flag"], fm["detail"]))
            if fm["flag"] not in ("N/A", "—", ""):
                print(f"    FORM: {fm['flag']} — {fm['detail']}")

            # K paper projection (all starters, locked now)
            kp = kmodel.project_k(
                g2["uw_whiff"], opp_team.get("rankings"), note=g2["verdict"]
            )
            db.lock_k(date, pid, name, kp)
            k_rows.append((label, name, kp))

            # HR boards for cleared arms
            if g2["verdict"].startswith(("TARGET", "ONE-PITCH")):
                mu_raw = puller.hr_matchup(
                    pid, opp_team["id"], season, vs_rhb=vec, vs_lhb=vec, offline=off
                )
                l15_raw = puller.hr_matchup(
                    pid,
                    opp_team["id"],
                    season,
                    vs_rhb=vec,
                    vs_lhb=vec,
                    range_value=15,
                    offline=off,
                )
                l10_raw = puller.hr_matchup(
                    pid,
                    opp_team["id"],
                    season,
                    vs_rhb=vec,
                    vs_lhb=vec,
                    range_value=10,
                    offline=off,
                )
                l5_raw = puller.hr_matchup(
                    pid,
                    opp_team["id"],
                    season,
                    vs_rhb=vec,
                    vs_lhb=vec,
                    range_value=5,
                    offline=off,
                )
                if not mu_raw:
                    db.log_alert(
                        date, "warn", f"{label}: matchup pull missing for {pid}"
                    )
                    continue
                mu = parsers.parse_hr_matchup(mu_raw)
                l15 = parsers.parse_hr_matchup(l15_raw) if l15_raw else None
                res = run_game(sp, pt, mu, l15)
                by10 = (
                    {b["id"]: b for b in parsers.parse_hr_matchup(l10_raw)[2]}
                    if l10_raw
                    else {}
                )
                by5 = (
                    {b["id"]: b for b in parsers.parse_hr_matchup(l5_raw)[2]}
                    if l5_raw
                    else {}
                )
                by15 = {b["id"]: b for b in (l15[2] if l15 else [])}
                lineup_ids = set(omap.keys())
                for rank, r in enumerate(res["board"], 1):
                    order = omap.get(r["id"])
                    if lineup_ids and r["id"] not in lineup_ids:
                        print(f"    SCRATCH: {r['name']} not in posted lineup — no bet")
                        continue
                    traj = gates.trajectory(
                        by15.get(r["id"]), by10.get(r["id"]), by5.get(r["id"])
                    )
                    r["traj"] = traj or ""
                    # v1.4 SHADOW: DTP profile tag off the L15 window — logged,
                    # displayed, ZERO effect on p / rank / promotes.
                    prof = profiles.classify(by15.get(r["id"]))
                    r["profile"] = prof
                    p = hr_prob(r, order)
                    if traj == "SUSTAINED":
                        p = min(0.25, p + 0.02)
                    elif traj == "HEATING":
                        p = min(0.25, p + 0.01)
                    elif traj == "COOLING":
                        p = max(0.08, p - 0.01)
                    lane = (
                        "thin"
                        if "THIN" in g2["verdict"]
                        else (
                            "one-pitch"
                            if g2["verdict"].startswith("ONE")
                            else "standard"
                        )
                    )
                    db.lock_board_row(
                        date,
                        label,
                        r["id"],
                        r["name"],
                        order or 0,
                        p,
                        breakeven(p),
                        rank,
                        rank,
                        (r.get("l15") or "")
                        + (("|" + traj) if traj else "")
                        + (("|P:" + prof) if prof else "")
                        + pen_tag,
                        "",
                        lane,
                    )  # v1.5: pen flag in ledger
                    # v1.4: best price across books + EV vs locked p
                    price, pbook = odds.best_price(book, r["id"], r["name"])
                    ev_val = odds.ev(p, price)
                    if price is not None:
                        db.lock_odds(date, r["id"], r["name"], pbook, price, p, ev_val)
                    # 7/27 hotfix B: dashboard shows resolved starter name
                    # (was res["pitcher"]["name"], which echoed the ID form)
                    slate_rows.append(
                        (
                            label,
                            name,
                            r["name"],
                            order,
                            p,
                            breakeven(p),
                            (r.get("l15") or "")
                            + pen_tag,  # v1.5: dashboard sees pen flag
                            lane,
                            prof,
                            odds.fmt_price(price),
                            ("" if ev_val is None else f"{ev_val:+.1%}"),
                            pbook or "",
                        )
                    )
                    print(
                        f"    {rank}. {r['name']:<22} slot {order or '?'}  "
                        f"{p:.0%}  be {breakeven(p)}  {r.get('l15') or ''}"
                        f"{(' ' + traj) if traj else ''}"
                        f"{(' ' + profiles.pretty(prof)) if prof else ''}"
                        f"{' ⚡PEN' if pen_tag else ''}"  # v1.5
                        f"{('  ' + odds.fmt_price(price) + ' ' + (pbook or '') + ' EV ' + f'{ev_val:+.1%}') if price is not None and ev_val is not None else ''}"
                    )

    render_dashboard(date, slate_rows, k_rows, alerts, form_rows)
    print(f"\nDashboard: out/dashboard.html   DB: {DB_FILE}   (rows locked {db.now()})")


def render_dashboard(date, rows, k_rows, alerts, form_rows=()):
    import profiles as _pr

    rows.sort(key=lambda r: -r[4])
    body_rows = "\n".join(
        f"<tr><td>{i + 1}</td><td><b>{r[2]}</b></td><td>{r[0]}</td><td>{r[1]}</td>"
        f"<td>{r[3] or '?'}</td><td class='p'>{r[4]:.0%}</td><td>{r[5]}</td>"
        f"<td>{'🔥' if 'LOUD' in r[6] else ('🌡' if ('NEAR' in r[6] or 'WARM' in r[6]) else ('❄' if 'QUIET' in r[6] else ''))}"
        f"{'📈' if 'HEATING' in r[6] or 'SUSTAINED' in r[6] else ('📉' if 'COOLING' in r[6] else '')}"
        f"{'⚡' if 'PEN-EDGE' in r[6] else ''}"
        f"</td><td>{_pr.pretty(r[8]) if len(r) > 8 and r[8] else ''}</td>"
        f"<td>{(r[9] + ' <small>' + r[11] + '</small>') if len(r) > 9 and r[9] else ''}</td>"
        f"<td class='{'p' if len(r) > 10 and r[10].startswith('+') else 'n'}'>"
        f"{r[10] if len(r) > 10 else ''}</td>"
        f"<td>{r[7]}</td></tr>"
        for i, r in enumerate(rows)
    )
    form_body = (
        "\n".join(
            f"<tr><td>{g}</td><td>{n}</td><td>{v}</td><td><b>{fl}</b></td><td>{d}</td></tr>"
            for g, n, v, fl, d in form_rows
        )
        or "<tr><td colspan='5'>—</td></tr>"
    )
    k_body = "\n".join(
        f"<tr><td>{g}</td><td>{n}</td><td class='p'>{kp['proj_k']}</td>"
        f"<td>{kp['range'][0]}–{kp['range'][1]}</td><td>{kp['arsenal_whiff']}%</td>"
        f"<td>{kp['opp_k_rank'] or '—'}</td><td>{kp['note']}</td></tr>"
        for g, n, kp in k_rows
    )
    alert_html = "".join(f"<li>{a}</li>" for a in alerts) or "<li>none</li>"
    html = f"""<!doctype html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>HR Engine — {date}</title><style>
body{{background:#0f1115;color:#e8e8e8;font:15px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;margin:1.2em}}
h1{{font-size:1.25em}} h2{{font-size:1.05em;margin-top:1.6em;color:#9ecbff}}
table{{border-collapse:collapse;width:100%;font-size:.92em}}
td,th{{padding:.4em .55em;border-bottom:1px solid #262a33;text-align:left}}
th{{color:#8a93a5;font-weight:600}} .p{{color:#7ee787;font-weight:700}}
.n{{color:#ff7b72;font-weight:700}}
.warn li{{color:#ffb86b}} .foot{{color:#666;font-size:.8em;margin-top:2em}}
.shadow{{color:#8a93a5;font-size:.85em}}
</style></head><body>
<h1>HR Engine — slate {date} <small>({C.CONFIG_VERSION})</small></h1>
<h2>⚠ Alerts — verify these by hand (5/27 rule)</h2><ul class='warn'>{alert_html}</ul>
<h2>HR Board (locked {db.now()})</h2>
<table><tr><th>#</th><th>Bat</th><th>Game</th><th>Pitcher</th><th>Slot</th>
<th>HR%</th><th>Breakeven</th><th>L15</th><th>Profile <span class='shadow'>(shadow)</span></th>
<th>Best price</th><th>EV</th><th>Lane</th></tr>{body_rows}</table>
<h2>Gate 2.5 — last-3-starts form <span class='shadow'>(SHADOW: annotates only,
season verdicts authoritative)</span></h2>
<table><tr><th>Game</th><th>Pitcher</th><th>Season verdict</th><th>Form flag</th>
<th>Detail</th></tr>{form_body}</table>
<h2>K Sheet — PAPER ONLY (locked at generation, no-peek rule)</h2>
<table><tr><th>Game</th><th>Pitcher</th><th>Proj K</th><th>Range</th>
<th>Arsenal whiff</th><th>Opp K rank</th><th>Verdict</th></tr>{k_body}</table>
<p class='foot'>Flat units. Sub-+400 book price = auto-pass. Exception lanes smallest unit.
Any book price LONGER than breakeven = model-positive; the EV column computes this
automatically from the book feed but the human STILL verifies the live price at
bet time (feeds go stale). Profile tags, Gate 2.5 flags, and PEN-EDGE (⚡) are
shadow lanes: logged for the ledger, zero ranking effect, graded after 5-6 slates
before earning weight. Human gates: starters, lineups, publish, sizing. Nothing
here is financial advice; it's a research log.</p>
</body></html>"""
    os.makedirs("out", exist_ok=True)
    with open("out/dashboard.html", "w") as f:
        f.write(html)


if __name__ == "__main__":
    main()
