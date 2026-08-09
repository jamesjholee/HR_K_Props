"""render.py — HR Engine dashboard renderer (v2 visual).

Drop-in replacement for the render_dashboard() function in run_morning.py.

Usage in run_morning.py:
    from render import render_dashboard        # add at top
    # ...and DELETE the old render_dashboard() function definition
    # (call site is unchanged)

Design: "night-game scoreboard" — deep green-black field, lamp-amber accent,
condensed caps for structure, mono tabular numerals for data. Signature:
per-row dot-matrix lamp meter for HR%. Interactive: search, game/lane/PEN
filters, sortable columns. Degraded lanes (dark odds feed, missing pens)
surface as visible alerts instead of silently blank columns.

Row tuple (unchanged from run_morning v1.5):
  (label, pitcher, bat, order, p, breakeven, l15+pen, lane, prof,
   price_str, ev_str, book)
"""

import html as _html
import os

# ---- soft imports so this module also works standalone (previews/tests) ----
try:
    from engine import config as _C

    _CONFIG_VERSION = getattr(_C, "CONFIG_VERSION", "")
except Exception:
    _CONFIG_VERSION = ""
try:
    import db as _db

    _now = _db.now
except Exception:
    from datetime import datetime, timezone

    def _now():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")


try:
    import profiles as _profiles

    def _pretty_prof(p):
        try:
            return _profiles.pretty(p) if p else ""
        except Exception:
            return p or ""
except Exception:

    def _pretty_prof(p):
        return p or ""


def _esc(s):
    return _html.escape(str(s), quote=True)


def _signal_chips(l15_raw):
    """Raw signal string ('LOUD SUSTAINED|PEN-EDGE') -> chip HTML."""
    s = (l15_raw or "").upper()
    chips = []
    if "LOUD" in s:
        chips.append('<span class="chip hot">LOUD</span>')
    elif "NEAR" in s:
        chips.append('<span class="chip warm">NEAR</span>')
    elif "WARM" in s:
        chips.append('<span class="chip warm">WARM</span>')
    elif "QUIET" in s:
        chips.append('<span class="chip cold">QUIET</span>')
    if "SUSTAINED" in s:
        chips.append('<span class="chip up">SUST &#8599;</span>')
    elif "HEATING" in s:
        chips.append('<span class="chip up">HEAT &#8599;</span>')
    elif "COOLING" in s:
        chips.append('<span class="chip down">COOL &#8600;</span>')
    if "PEN" in s:
        chips.append('<span class="chip pen">&#9889; PEN</span>')
    return "".join(chips)


def _lamp_meter(p):
    """HR prob -> 10-dot scoreboard lamp meter (cap 0.25 = full board)."""
    lit = max(0, min(10, round((p / 0.25) * 10)))
    dots = "".join(f'<i class="{"on" if i < lit else ""}"></i>' for i in range(10))
    return f'<span class="lamps" aria-hidden="true">{dots}</span>'


def _ev_cell(ev_str):
    if not ev_str:
        return '<td class="num dim">&mdash;</td>'
    cls = "pos" if ev_str.startswith("+") else "neg"
    return f'<td class="num {cls}" data-ev="{_esc(ev_str)}">{_esc(ev_str)}</td>'


def _price_cell(price_str, book):
    if not price_str:
        return '<td class="num dim">&mdash;</td>'
    stale = "stale" in (book or "").lower()
    book_clean = _esc((book or "").replace("\u26a0", "").replace("stale", "").strip())
    stale_chip = '<span class="chip stale">STALE</span>' if stale else ""
    return (
        f'<td class="num">{_esc(price_str)}'
        f'<span class="book">{book_clean}</span>{stale_chip}</td>'
    )


def _et_clock(iso):
    """ISO UTC -> compact ET clock, '1:15p'. Empty string on any failure."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(
            ZoneInfo("America/New_York")
        )
        return f"{(dt.hour - 1) % 12 + 1}:{dt.minute:02d}{'a' if dt.hour < 12 else 'p'}"
    except Exception:
        return ""


def _record_strip(season):
    """Season-to-date record strip HTML from track.py's season.json dict.

    Numbers come exclusively from the graded ledger (slate_ledger) — the
    renderer computes nothing, so the strip can't drift from the record.
    Returns '' when no season data supplied (backward compatible).
    """
    if not season:
        return ""
    try:
        s, l5, cc = season["season"], season["last5"], season["current_config"]
        graded = [x for x in season.get("slates", [])
                  if x.get("pct") is not None][-10:]
        # tiny text sparkline: block height per slate hit%, amber >= 12%
        blocks = "▁▂▃▄▅▆▇█"
        spark = "".join(
            f'<span class="hi">{blocks[min(7, int(x["pct"] // 2.5))]}</span>'
            if x["pct"] >= 12 else blocks[min(7, int(x["pct"] // 2.5))]
            for x in graded
        )
        cfg_short = (cc.get("version") or "?").split("-")[0]
        return (
            '<div class="recstrip">'
            f'<span>SEASON <b>{s["hits"]}/{s["boarded"]}</b> '
            f'({s["pct"]}%)</span>'
            f'<span>LAST 5 <b>{l5["hits"]}/{l5["boarded"]}</b> '
            f'({l5["pct"]}%)</span>'
            f'<span>{_esc(cfg_short)} <b>{cc["hits"]}/{cc["boarded"]}</b> '
            f'({cc["pct"]}% · {cc["slates"]} slates)</span>'
            f'<span class="spark">{spark}</span>'
            '<span class="tag">graded ledger · breakeven ~15&ndash;18% '
            '&middot; research log, not advice</span>'
            "</div>"
        )
    except (KeyError, TypeError):
        return ""


def render_dashboard(date, rows, k_rows, alerts, form_rows=(), game_times=None,
                     season=None):
    # season: dict loaded from out/season.json (track.py). Optional — old
    # callers unaffected. Renders the season-to-date record strip.
    rows = sorted(rows, key=lambda r: -r[4])
    # 7/29: games ordered by start time when run_morning supplies it;
    # unknown-time games sink to the end alphabetically.
    _gt = game_times or {}

    def _gkey(label):
        return (_gt.get(label) or "9999", label)

    games = sorted({r[0] for r in rows}, key=_gkey)
    k_rows = sorted(k_rows, key=lambda t: _gkey(t[0]))
    form_rows = sorted(form_rows, key=lambda t: _gkey(t[0]))
    lanes = sorted({r[7] for r in rows})

    # render-side safety net: degraded lanes become visible alerts
    alerts = list(alerts)
    if rows and all(not r[9] for r in rows):
        alerts.append(
            "odds feed dark — no prices on this board; "
            "EV lane blank (verify prices manually)"
        )

    # ---- board rows ----
    body = []
    for i, r in enumerate(rows, 1):
        (
            label,
            pitcher,
            bat,
            order,
            p,
            be,
            sig,
            lane,
            prof,
            price,
            ev,
            book,
            insight,
        ) = (list(r) + [""] * 13)[:13]
        prof_txt = _pretty_prof(prof)
        pen = "1" if "PEN" in (sig or "").upper() else "0"
        body.append(
            f'<tr data-game="{_esc(label)}" data-lane="{_esc(lane)}" '
            f'data-pen="{pen}" data-p="{p}" data-be="{be}" '
            f'data-insight="{_esc(insight)}" '
            f'data-search="{_esc((bat + " " + pitcher + " " + label).lower())}">'
            f'<td class="num rk">{i}</td>'
            f'<td class="bat"><b>{_esc(bat)}</b>'
            f"{f'<span class=prof>{_esc(prof_txt)}</span>' if prof_txt else ''}</td>"
            f'<td class="game">{_esc(label)}</td>'
            f'<td class="pit">{_esc(pitcher)}</td>'
            f'<td class="num">{_esc(order) if order else "&mdash;"}</td>'
            f'<td class="num pcell"><span class="pval">{p:.0%}</span>{_lamp_meter(p)}</td>'
            f'<td class="num">{_esc(be)}</td>'
            f"{_price_cell(price, book)}"
            f"{_ev_cell(ev)}"
            f'<td class="sig">{_signal_chips(sig)}</td>'
            f'<td><span class="lane l-{_esc(lane)}">{_esc(lane)}</span></td>'
            f"</tr>"
        )
    body_rows = "\n".join(body)

    game_chips = "".join(
        f'<button class="fchip" data-fgame="{_esc(g)}">{_esc(g)}'
        + (
            f'<span class="ftime">{_esc(_et_clock(_gt[g]))}</span>'
            if _gt.get(g) and _et_clock(_gt[g])
            else ""
        )
        + "</button>"
        for g in games
    )
    lane_chips = "".join(
        f'<button class="fchip" data-flane="{_esc(l)}">{_esc(l)}</button>'
        for l in lanes
    )

    form_body = (
        "\n".join(
            f'<tr><td class="game">{_esc(g)}</td><td class="pit">{_esc(n)}</td>'
            f'<td>{_esc(v)}</td><td class="flag f-{_esc(fl).replace("?", "").replace(" ", "-")}">'
            f'<b>{_esc(fl)}</b></td><td class="dim">{_esc(d)}</td></tr>'
            for g, n, v, fl, d in form_rows
        )
        or '<tr><td colspan="5" class="dim">&mdash;</td></tr>'
    )

    k_body = "\n".join(
        f'<tr><td class="game">{_esc(g)}</td><td class="pit">{_esc(n)}</td>'
        f'<td class="num kbig">{_esc(kp["proj_k"])}</td>'
        f'<td class="num">{_esc(kp["range"][0])}&ndash;{_esc(kp["range"][1])}</td>'
        f'<td class="num">{_esc(kp["arsenal_whiff"])}%</td>'
        f'<td class="num">{_esc(kp["opp_k_rank"] or "&mdash;")}</td>'
        f"<td>{_esc(kp['note'])}</td></tr>"
        for g, n, kp in k_rows
    )

    alert_html = (
        "".join(f"<li>{_esc(a)}</li>" for a in alerts) or '<li class="ok">none</li>'
    )
    n_alerts = len(alerts)
    lock_time = _now()
    cfg = _CONFIG_VERSION

    html_out = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HR Engine &mdash; {_esc(date)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{{
  --field:#0C110F; --panel:#131A16; --panel2:#0F1512; --line:#24312A;
  --ink:#EDE9DC; --dim:#8C978D; --amber:#F3A83B; --amber-dim:#8a6524;
  --pos:#7ED99A; --neg:#F27A70; --cold:#7FA8C9;
  --disp:"Barlow Condensed",Arial Narrow,sans-serif;
  --body:"IBM Plex Sans",-apple-system,Segoe UI,sans-serif;
  --mono:"IBM Plex Mono",SFMono-Regular,Menlo,monospace;
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--field);color:var(--ink);
  font:14px/1.5 var(--body);
  background-image:radial-gradient(ellipse 80% 50% at 50% -10%,#16221b 0%,transparent 60%)}}
a{{color:var(--amber)}}

/* ---- topbar: the scoreboard ---- */
.topbar{{position:sticky;top:0;z-index:30;background:linear-gradient(180deg,#0e1411,#0c110fE6);
  backdrop-filter:blur(6px);border-bottom:2px solid var(--amber-dim);
  padding:.7em 1.2em;display:flex;flex-wrap:wrap;align-items:baseline;gap:.5em 1.2em}}
.topbar h1{{margin:0;font:700 1.7em/1 var(--disp);letter-spacing:.06em;
  text-transform:uppercase}}
.topbar h1 .amber{{color:var(--amber)}}
.meta{{font:500 .82em/1.4 var(--mono);color:var(--dim);display:flex;gap:1.2em;flex-wrap:wrap}}
.meta b{{color:var(--ink);font-weight:600}}
.alertpill{{font:600 .78em/1 var(--mono);color:#100b02;background:var(--amber);
  border-radius:99px;padding:.35em .7em;margin-left:auto}}
/* ---- season record strip (track.py -> season.json) ---- */
.recstrip{{display:flex;gap:1.6em;flex-wrap:wrap;align-items:baseline;
  font:500 .8em/1.5 var(--mono);color:var(--dim);
  background:#0d1310;border-bottom:1px solid #1c2620;padding:.5em 1.2em}}
.recstrip b{{color:var(--ink);font-weight:600}}
.recstrip .spark{{letter-spacing:.12em;color:var(--dim)}}
.recstrip .spark .hi{{color:var(--amber)}}
.recstrip .tag{{font-size:.9em;opacity:.75}}
.alertpill.zero{{background:var(--line);color:var(--dim)}}

.wrap{{max-width:1180px;margin:0 auto;padding:1.2em}}

/* ---- alerts ---- */
.alerts{{border:1px solid var(--amber-dim);border-left:4px solid var(--amber);
  background:linear-gradient(90deg,#1c1607,var(--panel2));border-radius:8px;
  padding:.8em 1.1em;margin:0 0 1.4em}}
.alerts h2{{margin:0 0 .3em;font:600 .95em var(--disp);letter-spacing:.12em;
  text-transform:uppercase;color:var(--amber)}}
.alerts ul{{margin:0;padding-left:1.2em;font:.86em/1.6 var(--mono)}}
.alerts .ok{{color:var(--dim);list-style:none;margin-left:-1.2em}}

/* ---- section headers ---- */
h2.sec{{font:600 1.15em var(--disp);letter-spacing:.14em;text-transform:uppercase;
  color:var(--ink);margin:1.8em 0 .6em;display:flex;align-items:center;gap:.6em}}
h2.sec::after{{content:"";flex:1;height:1px;background:var(--line)}}
h2.sec small{{font:500 .62em var(--mono);letter-spacing:0;text-transform:none;color:var(--dim)}}

/* ---- filter bar ---- */
.controls{{display:flex;flex-wrap:wrap;gap:.5em;align-items:center;margin:0 0 .8em}}
.controls input[type=search]{{background:var(--panel);border:1px solid var(--line);
  color:var(--ink);border-radius:6px;padding:.45em .7em;font:.85em var(--mono);
  min-width:180px}}
.controls input[type=search]:focus{{outline:2px solid var(--amber);outline-offset:1px}}
.fchip{{background:var(--panel);border:1px solid var(--line);color:var(--dim);
  border-radius:99px;padding:.35em .75em;font:600 .74em var(--mono);cursor:pointer}}
.fchip:hover{{border-color:var(--amber-dim);color:var(--ink)}}
.fchip.on{{background:var(--amber);border-color:var(--amber);color:#100b02}}
.fchip:focus-visible{{outline:2px solid var(--amber);outline-offset:2px}}
.fchip .ftime{{margin-left:.45em;opacity:.55;font-size:.85em;letter-spacing:.02em}}
.fchip.on .ftime{{opacity:.8}}
#board tbody tr{{cursor:pointer}}
#board tbody tr.sel td{{background:rgba(255,176,32,.07);box-shadow:inset 3px 0 0 var(--amber)}}
.detail{{background:var(--panel);border:1px solid var(--amber-dim);border-radius:8px;
  padding:.8em 1em;margin:.6em 0 .9em}}
.dhead{{display:flex;align-items:baseline;gap:.8em}}
.dbat{{font:700 1.05em var(--disp);letter-spacing:.06em;color:var(--amber)}}
.dmeta{{color:var(--dim);font-size:.85em}}
.dclose{{margin-left:auto;background:none;border:none;color:var(--dim);
  font-size:1.2em;cursor:pointer;line-height:1}}
.dclose:hover{{color:var(--ink)}}
.dtext{{margin:.5em 0 .2em;line-height:1.55}}
.dnote{{margin:0;color:var(--dim);font-size:.75em;opacity:.7}}
.count{{margin-left:auto;font:500 .8em var(--mono);color:var(--dim)}}
.fdivider{{width:1px;height:1.4em;background:var(--line)}}

/* ---- tables ---- */
.tblwrap{{overflow-x:auto;border:1px solid var(--line);border-radius:10px;
  background:var(--panel2)}}
table{{border-collapse:collapse;width:100%;min-width:900px;font-size:.9em}}
th,td{{padding:.5em .65em;border-bottom:1px solid var(--line);text-align:left;
  vertical-align:middle;white-space:nowrap}}
thead th{{position:sticky;top:0;background:var(--panel);color:var(--dim);
  font:600 .78em var(--disp);letter-spacing:.12em;text-transform:uppercase;z-index:5}}
th.sortable{{cursor:pointer;user-select:none}}
th.sortable:hover{{color:var(--ink)}}
th.sorted::after{{content:" \\25BE";color:var(--amber)}}
tbody tr:hover{{background:#182019}}
tbody tr:last-child td{{border-bottom:0}}
td.num,th.num{{font-family:var(--mono);font-variant-numeric:tabular-nums;text-align:right}}
td.rk{{color:var(--dim);font-size:.85em;width:2.2em}}
td.bat b{{font-weight:600;letter-spacing:.01em}}
td.game{{font:600 .85em var(--disp);letter-spacing:.08em;color:var(--dim)}}
td.pit{{color:var(--dim)}}
.prof{{display:block;font:500 .72em var(--mono);color:var(--amber);margin-top:.1em}}
.book{{display:inline-block;font:.72em var(--mono);color:var(--dim);margin-left:.5em}}
.dim{{color:var(--dim)}}
.pos{{color:var(--pos);font-weight:600}} .neg{{color:var(--neg)}}
td.kbig{{color:var(--amber);font-weight:600;font-size:1.05em}}

/* ---- the lamp meter (signature) ---- */
.pcell{{min-width:9.5em}}
.pval{{display:inline-block;width:3em;font-weight:600}}
.lamps{{display:inline-flex;gap:2px;margin-left:.5em;vertical-align:middle}}
.lamps i{{width:6px;height:6px;border-radius:50%;background:#1d2822;
  border:1px solid #263229}}
.lamps i.on{{background:var(--amber);border-color:var(--amber);
  box-shadow:0 0 5px 0 #f3a83b66}}

/* ---- chips ---- */
.chip{{display:inline-block;font:600 .68em var(--mono);letter-spacing:.05em;
  border-radius:4px;padding:.2em .45em;margin-right:.3em;border:1px solid transparent}}
.chip.hot{{color:var(--amber);border-color:var(--amber-dim);background:#f3a83b14}}
.chip.warm{{color:#d9b98a;border-color:#4a3d26;background:#d9b98a0d}}
.chip.cold{{color:var(--cold);border-color:#2a3b47;background:#7fa8c910}}
.chip.up{{color:var(--pos);border-color:#25402f;background:#7ed99a10}}
.chip.down{{color:var(--cold);border-color:#2a3b47}}
.chip.pen{{color:var(--amber);border-color:var(--amber-dim)}}
.chip.stale{{color:#100b02;background:#c9a24b;margin-left:.4em;font-size:.62em}}
.lane{{font:600 .7em var(--mono);color:var(--dim);border:1px solid var(--line);
  border-radius:4px;padding:.2em .5em}}
.l-thin{{color:#d9b98a}} .l-one-pitch{{color:var(--cold)}} .l-watch{{color:var(--amber)}}
.flag b{{font:600 .85em var(--mono)}}
.f-CONFIRMED b{{color:var(--pos)}} .f-DECLINING-TARGET b{{color:var(--neg)}}

details.legend{{margin:0 0 1.4em;border:1px solid var(--line);border-radius:10px;
  background:var(--panel2);padding:.2em 1.1em}}
details.legend[open]{{padding-bottom:1em}}
details.legend h2.sec{{margin:.6em 0}}
.legendgrid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));
  gap:.4em 1.6em}}
.legendgrid h3{{font:600 .8em var(--disp);letter-spacing:.12em;text-transform:uppercase;
  color:var(--amber);margin:.6em 0 .2em}}
.legendgrid p{{margin:.2em 0;font-size:.85em;line-height:1.55;color:var(--dim)}}
.legendgrid p b{{color:var(--ink)}}
details{{margin-top:1.6em}}
details summary{{cursor:pointer;list-style:none}}
details summary::-webkit-details-marker{{display:none}}
details summary h2.sec::before{{content:"\\25B8";color:var(--amber);
  font-size:.8em;transition:transform .15s}}
details[open] summary h2.sec::before{{transform:rotate(90deg)}}
@media (prefers-reduced-motion:reduce){{*{{transition:none!important}}}}

.foot{{color:var(--dim);font:.78em/1.7 var(--mono);margin:2.5em 0 1em;
  border-top:1px solid var(--line);padding-top:1.2em}}
@media(max-width:640px){{
  .topbar h1{{font-size:1.35em}}
  .wrap{{padding:.8em}}
  table{{font-size:.84em}}
}}
</style></head><body>

<header class="topbar">
  <h1>HR <span class="amber">/</span> K Engine</h1>
  <div class="meta">
    <span>SLATE <b>{_esc(date)}</b></span>
    <span>CFG <b>{_esc(cfg)}</b></span>
    <span>LOCKED <b>{_esc(lock_time)}</b></span>
  </div>
  <span class="alertpill{" zero" if n_alerts == 0 else ""}">{n_alerts} ALERT{"S" if n_alerts != 1 else ""}</span>
</header>
{_record_strip(season)}
<div class="wrap">

<div class="alerts">
  <h2>Verify by hand &mdash; 5/27 rule</h2>
  <ul>{alert_html}</ul>
</div>

<details class="legend"><summary>
<h2 class="sec">How to Read This Board <small>first time here? open this</small></h2>
</summary>
<div class="legendgrid">
<div><h3>What this is</h3>
<p>A nightly MLB home-run prop <b>research log</b>. A model ranks hitters by
estimated HR probability for tonight's slate, the board locks with a
timestamp before games start, and results are graded after. Every lock and
grade is committed publicly &mdash; the record can't be edited after the fact.
Nothing here is betting advice.</p></div>
<div><h3>HR% &amp; lamps</h3>
<p><b>HR%</b> is the model's probability this bat homers tonight (capped at
25%). The amber lamp meter fills with it &mdash; a full board is the cap.
For context: even elite hitters homer in roughly 1 of 6 games.</p></div>
<div><h3>BE &middot; Price &middot; EV</h3>
<p><b>BE</b> (breakeven) is the minimum odds that make the model's HR%
profitable. <b>Price</b> is the best sportsbook line found (main line, 1+ HR),
with the book named. <b>EV</b> compares them: positive means the price is
longer than breakeven <i>per the model</i>. <span class="chip stale">STALE</span>
means the quote is over 2 hours old &mdash; treat its EV as unverified.</p></div>
<div><h3>Signal chips</h3>
<p><span class="chip hot">LOUD</span> <span class="chip warm">NEAR</span>
<span class="chip cold">QUIET</span> = how hard the bat's recent contact has
been (last-15-games batted-ball screen).
<span class="chip up">HEAT &#8599;</span> <span class="chip up">SUST &#8599;</span>
<span class="chip down">COOL &#8600;</span> = the trend across 5/10/15-game
windows. <span class="chip pen">&#9889; PEN</span> = the opposing bullpen is
fatigued (worked hard recently) &mdash; late-game HR exposure.</p></div>
<div><h3>Lane</h3>
<p><b>standard</b>: full-confidence pitcher read. <b>thin</b>: the pitcher has
a small sample this season (&lt;200 batted balls) &mdash; read with caution.
<b>one-pitch</b>: the pitcher is only vulnerable on one pitch type &mdash;
conditional matchup. <b>watch</b>: cleared the same quality gates but fell
outside board width or the price floor &mdash; tracked because this lane has
historically outperformed the main board.</p></div>
<div><h3>Shadow lanes</h3>
<p>Profile tags (&#128163; &#128640; &#127808; &#127919;), form flags, and
&#9889;PEN are <b>shadow signals</b>: logged and displayed for evaluation but
given <i>zero weight</i> in rankings until they've proven out over 5&ndash;6
graded slates. That's the house rule: evidence moves the model, not instinct.</p></div>
</div>
</details>

<h2 class="sec">HR Board <small>{len(rows)} bats &middot; sorted by model HR%</small></h2>

<div class="controls">
  <input type="search" id="q" placeholder="search bat / pitcher / game" aria-label="Search board">
  <span class="fdivider"></span>
  {game_chips}
  <span class="fdivider"></span>
  {lane_chips}
  <button class="fchip" id="penonly" data-fpen="1">&#9889; PEN only</button>
  <span class="count" id="count"></span>
</div>

<div id="detail" class="detail" hidden>
  <div class="dhead">
    <span class="dbat" id="dbat"></span>
    <span class="dmeta" id="dmeta"></span>
    <button class="dclose" id="dclose" aria-label="close">&times;</button>
  </div>
  <p class="dtext" id="dtext"></p>
  <p class="dnote">read locked at generation &middot; research log, not advice</p>
</div>

<div class="tblwrap">
<table id="board">
<thead><tr>
  <th class="num">#</th><th>Bat</th><th>Game</th><th>Pitcher</th>
  <th class="num">Slot</th>
  <th class="num sortable sorted" data-sort="p">HR%</th>
  <th class="num sortable" data-sort="be">BE</th>
  <th class="num">Price</th>
  <th class="num sortable" data-sort="ev">EV</th>
  <th>Signals</th><th>Lane</th>
</tr></thead>
<tbody>{body_rows}</tbody>
</table>
</div>

<details open><summary>
<h2 class="sec">Gate 2.5 &mdash; Last-3-Starts Form <small>shadow: annotates only, season verdicts authoritative</small></h2>
</summary>
<div class="tblwrap"><table>
<thead><tr><th>Game</th><th>Pitcher</th><th>Season Verdict</th><th>Form Flag</th><th>Detail</th></tr></thead>
<tbody>{form_body}</tbody>
</table></div>
</details>

<details open><summary>
<h2 class="sec">K Sheet <small>paper only &middot; locked at generation &middot; no-peek rule</small></h2>
</summary>
<div class="tblwrap"><table>
<thead><tr><th>Game</th><th>Pitcher</th><th class="num">Proj K</th><th class="num">Range</th>
<th class="num">Arsenal Whiff</th><th class="num">Opp K Rank</th><th>Verdict</th></tr></thead>
<tbody>{k_body}</tbody>
</table></div>
</details>

<p class="foot">Flat units. Sub-+400 book price = auto-pass. Exception lanes smallest unit.
Any book price LONGER than breakeven = model-positive; the EV column computes this
automatically from the book feed but the human STILL verifies the live price at
bet time (feeds go stale). Profile tags, Gate 2.5 flags, and PEN-EDGE lamps are
shadow lanes: logged for the ledger, zero ranking effect, graded after 5&ndash;6 slates
before earning weight. Human gates: starters, lineups, publish, sizing. Nothing
here is financial advice; it's a research log.</p>

</div>

<script>
(function(){{
  var q=document.getElementById('q'),
      chips=[].slice.call(document.querySelectorAll('.fchip')),
      rows=[].slice.call(document.querySelectorAll('#board tbody tr')),
      count=document.getElementById('count'),
      fGame=null,fLane=null,fPen=false;

  function apply(){{
    var term=(q.value||'').toLowerCase(),shown=0;
    rows.forEach(function(tr){{
      var ok=true;
      if(fGame&&tr.dataset.game!==fGame)ok=false;
      if(fLane&&tr.dataset.lane!==fLane)ok=false;
      if(fPen&&tr.dataset.pen!=='1')ok=false;
      if(term&&tr.dataset.search.indexOf(term)<0)ok=false;
      tr.style.display=ok?'':'none';
      if(ok)shown++;
    }});
    count.textContent=shown+' / '+rows.length+' bats';
  }}

  chips.forEach(function(c){{
    c.addEventListener('click',function(){{
      if(c.dataset.fgame!==undefined){{
        fGame=(fGame===c.dataset.fgame)?null:c.dataset.fgame;
        chips.forEach(function(x){{if(x.dataset.fgame!==undefined)x.classList.toggle('on',x.dataset.fgame===fGame);}});
      }}else if(c.dataset.flane!==undefined){{
        fLane=(fLane===c.dataset.flane)?null:c.dataset.flane;
        chips.forEach(function(x){{if(x.dataset.flane!==undefined)x.classList.toggle('on',x.dataset.flane===fLane);}});
      }}else if(c.dataset.fpen!==undefined){{
        fPen=!fPen;c.classList.toggle('on',fPen);
      }}
      apply();
    }});
  }});
  q.addEventListener('input',apply);

  // 7/29: click a bat row -> pinned detail panel (survives sort/filter)
  var dPanel=document.getElementById('detail'),dBat=document.getElementById('dbat'),
      dMeta=document.getElementById('dmeta'),dText=document.getElementById('dtext'),
      dSel=null;
  function closeDetail(){{
    dPanel.hidden=true;
    if(dSel)dSel.classList.remove('sel');
    dSel=null;
  }}
  document.getElementById('dclose').addEventListener('click',closeDetail);
  rows.forEach(function(tr){{
    tr.addEventListener('click',function(){{
      if(dSel===tr){{closeDetail();return;}}
      if(dSel)dSel.classList.remove('sel');
      dSel=tr;tr.classList.add('sel');
      var tds=tr.querySelectorAll('td');
      dBat.textContent=tds[1]?tds[1].textContent:'';
      dMeta.textContent=tr.dataset.game+' \u00b7 vs '+(tds[3]?tds[3].textContent:'')
        +(tds[7]&&tds[7].textContent.trim()?' \u00b7 '+tds[7].textContent.trim():'');
      dText.textContent=tr.dataset.insight||'no read locked for this bat';
      dPanel.hidden=false;
      dPanel.scrollIntoView({{block:'nearest',behavior:'smooth'}});
    }});
  }});

  // column sort (HR%, BE, EV)
  var tbody=document.querySelector('#board tbody');
  function num(v){{v=(v||'').toString().replace(/[+%,]/g,'');var f=parseFloat(v);return isNaN(f)?-1e9:f;}}
  [].slice.call(document.querySelectorAll('th.sortable')).forEach(function(th){{
    th.addEventListener('click',function(){{
      var key=th.dataset.sort,dir=th.classList.contains('sorted')&&!th.classList.contains('asc')?1:-1;
      document.querySelectorAll('th.sortable').forEach(function(x){{x.classList.remove('sorted','asc');}});
      th.classList.add('sorted');if(dir===1)th.classList.add('asc');
      var sorted=rows.slice().sort(function(a,b){{
        var va,vb;
        if(key==='p'){{va=+a.dataset.p;vb=+b.dataset.p;}}
        else if(key==='be'){{va=num(a.dataset.be);vb=num(b.dataset.be);}}
        else{{va=num((a.querySelector('[data-ev]')||{{}}).dataset&&a.querySelector('[data-ev]')?a.querySelector('[data-ev]').dataset.ev:'');
              vb=num(b.querySelector('[data-ev]')?b.querySelector('[data-ev]').dataset.ev:'');}}
        return dir*(vb-va);
      }});
      sorted.forEach(function(tr){{tbody.appendChild(tr);}});
    }});
  }});
  apply();
}})();
</script>
</body></html>"""
    html_out = html_out.replace("</body></html>",
                            _LINEUP_OVERLAY.replace("__SLATE_DATE__", str(date)))
    os.makedirs("out", exist_ok=True)
    with open("out/dashboard.html", "w") as f:
        f.write(html_out)


# --------------------------------------------------------------------------
# Lineup badges (v1.6.4) — display-only overlay fed by precheck's
# out/lineup_status.json. Bats are ANNOTATED, never removed: scratched rows
# dim + strike but remain fully visible (the locked board stays the record).
# Plain string (NOT an f-string) so raw CSS/JS braces are safe.
_LINEUP_OVERLAY = """<style>
.lu{margin-left:6px;font-size:10px;padding:1px 5px;border-radius:8px;white-space:nowrap}
.lu.in{background:#123d1f;color:#5fd57f}
.lu.out{background:#43181c;color:#ff8d94}
tr.lu-out td{opacity:.55}
tr.lu-out .bat b{text-decoration:line-through;text-decoration-thickness:1px}
#lu-stamp{font-size:10px;opacity:.6;margin-left:8px}
</style>
<script>
(function(){
  const norm=s=>(s||"").normalize("NFD").replace(/[\\u0300-\\u036f]/g,"").toLowerCase().trim();
  const ord=n=>n+({1:"st",2:"nd",3:"rd"}[[11,12,13].includes(n%100)?0:n%10]||"th");
  const SLATE="__SLATE_DATE__";
  function clearAll(){
    document.querySelectorAll(".lu").forEach(e=>e.remove());
    document.querySelectorAll("tr.lu-out").forEach(tr=>tr.classList.remove("lu-out"));
    const st=document.getElementById("lu-stamp"); if(st) st.textContent="";
  }
  function apply(d){
    if(!d||!d.bats) return;
    if(d.date!==SLATE){clearAll();return;}  // stale slate: never paint yesterday onto today
    document.querySelectorAll("tr[data-search]").forEach(tr=>{
      const b=tr.querySelector(".bat b"); if(!b) return;
      const rec=d.bats[norm(b.textContent)];
      tr.classList.remove("lu-out");
      tr.querySelectorAll(".lu").forEach(e=>e.remove());
      if(!rec) return;
      const tag=document.createElement("span");
      if(rec.st==="in"){tag.className="lu in";tag.textContent="\\u2713 "+(rec.slot?ord(rec.slot):"in");}
      else{tag.className="lu out";tag.textContent="\\u2717 not in lineup";tr.classList.add("lu-out");}
      b.after(tag);
    });
    let st=document.getElementById("lu-stamp");
    if(!st){st=document.createElement("span");st.id="lu-stamp";
      (document.querySelector("h1")||document.body).appendChild(st);}
    st.textContent="lineups checked "+new Date(d.updated_at).toLocaleTimeString(
      [],{hour:"2-digit",minute:"2-digit"});
  }
  function tick(){
    fetch("https://raw.githubusercontent.com/jamesjholee/HR_K_Props/main/out/lineup_status.json?t="+Date.now()).then(r=>r.ok?r.json():null)
      .then(apply).catch(()=>{});
  }
  tick(); setInterval(tick, 5*60*1000);
})();
</script>
</body></html>"""
