"""
precheck.py — automated pre-game verification sweeps (the 5/27 rule, loud).

Runs from a frequent Actions cron; decides internally whether a check is DUE:

  T-90  once per slate, 90 min before the slate's FIRST pitch:
        slate-wide sweep — game status (postponed/delayed), probable
        starters vs the arms the board was scored against. Catches
        deadline moves / early scratches while there's time to act.
  T-30  once per game, ~30 min before THAT game's first pitch:
        final check — probable starter still matches, lineup posted,
        every boarded bat present in the lineup (with batting order).

This is an ALERT layer, not a replacement for the human gate: starter and
lineup verification via PropFinder + the MLB app remains mandatory. The
script never modifies hr_board or dashboard.html (no-peek preserved) — it
only appends to precheck_log, writes out/prechecks/precheck_<date>.md, and
signals LOUD findings via exit code 2 (the workflow opens a GitHub issue).

Checks are skipped, never run, after a game's first pitch.

Usage:
  python3 precheck.py                     # cron mode: run whatever is due
  python3 precheck.py --date 2026-08-03
  python3 precheck.py --force t90         # manual dispatch: run regardless
  python3 precheck.py --force t30:824647
"""
import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

UA = {"User-Agent": "hr-engine-precheck/1.0"}
SAPI = "https://statsapi.mlb.com/api/v1"
ET = ZoneInfo("America/New_York")

T90_LEAD = timedelta(minutes=90)
T30_LEAD = timedelta(minutes=35)   # cron is */15 — fire inside [T-35, pitch)

DDL = """
CREATE TABLE IF NOT EXISTS precheck_log(
    slate_date TEXT,
    check_id   TEXT,      -- 'T90' or 'T30:<gamePk>'
    loud       INTEGER,   -- 1 if any finding needs human attention
    findings   TEXT,
    run_at     TEXT,
    PRIMARY KEY (slate_date, check_id)
)
"""


def get(url, **params):
    """Same 3-try backoff contract as grade_hrs.get."""
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
    raise last


def fetch_schedule(date):
    return get(f"{SAPI}/schedule", sportId=1, date=date,
               hydrate="probablePitcher")


def fetch_boxscore(game_pk):
    return get(f"{SAPI}/game/{game_pk}/boxscore")


# --------------------------------------------------------------------------
def board_games(con, date):
    """{game_pk: {'label', 'bats': [(id, name)]}} for the locked board."""
    out = {}
    for label, pk, bid, bname in con.execute(
        """SELECT game, game_pk, batter_id, batter_name
           FROM hr_board WHERE slate_date=? AND game_pk>0""", (date,)):
        g = out.setdefault(pk, {"label": label, "bats": []})
        g["bats"].append((bid, bname))
    return out


def scored_arms(con, date):
    return {r[0] for r in con.execute(
        "SELECT pitcher_id FROM pitcher_verdicts WHERE slate_date=?", (date,))}


def sched_index(sched):
    """{gamePk: {'start': dt, 'status', 'probables': {side: (id, name)}}}"""
    idx = {}
    for d in sched.get("dates", []):
        for g in d.get("games", []):
            probables = {}
            for side in ("home", "away"):
                pp = (g["teams"][side].get("probablePitcher") or {})
                if pp.get("id"):
                    probables[side] = (pp["id"], pp.get("fullName", "?"))
            idx[g["gamePk"]] = {
                "start": datetime.fromisoformat(
                    g["gameDate"].replace("Z", "+00:00")),
                "status": (g.get("status") or {}).get(
                    "detailedState", "Unknown"),
                "probables": probables,
            }
    return idx


# --------------------------------------------------------------------------
def check_t90(board, arms, sidx):
    """Slate-wide sweep. Returns (loud, lines)."""
    loud, lines = False, []
    for pk, g in sorted(board.items(), key=lambda x: sidx.get(
            x[0], {}).get("start", datetime.max.replace(tzinfo=timezone.utc))):
        s = sidx.get(pk)
        if not s:
            lines.append(f"⚠ {g['label']} (pk {pk}): NOT IN MLB SCHEDULE "
                         "— verify game still on")
            loud = True
            continue
        st = s["status"]
        if any(w in st for w in ("Postponed", "Suspended", "Cancelled")):
            lines.append(f"⚠ {g['label']}: status = {st} "
                         f"— {len(g['bats'])} boarded bats DEAD")
            loud = True
            continue
        elif "Delayed" in st:
            lines.append(f"⚠ {g['label']}: status = {st} — watch start time")
            loud = True
        probs = s["probables"]
        if len(probs) < 2:
            lines.append(f"⚠ {g['label']}: probable starter TBD "
                         f"({len(probs)}/2 listed) — 5/27 RULE, verify in "
                         "MLB app before betting this game")
            loud = True
        for side, (pid, pname) in probs.items():
            if pid not in arms:
                lines.append(
                    f"⚠ {g['label']}: {side} probable {pname} (id {pid}) was "
                    f"NOT SCORED at lock — scratch/trade signature. Bats "
                    f"boarded vs the other arm may be unaffected; bats "
                    f"scored VS this slot are built on a dead read.")
                loud = True
        if s and len(probs) == 2 and all(p[0] in arms for p in probs.values()):
            lines.append(f"✓ {g['label']}: both probables match locked arms")
    return loud, lines


def check_t30(pk, g, arms, sidx, box):
    """Per-game final check. Returns (loud, lines)."""
    loud, lines = False, []
    s = sidx.get(pk, {})
    for side, (pid, pname) in s.get("probables", {}).items():
        if pid not in arms:
            lines.append(f"⚠ starter {pname} (id {pid}, {side}) not among "
                         "locked scored arms — verify matchup basis")
            loud = True
    lineup, posted_sides = {}, 0
    try:
        for side in ("home", "away"):
            side_n = 0
            for key, pl in (box["teams"][side]["players"] or {}).items():
                bo = pl.get("battingOrder")
                if bo:
                    side_n += 1
                    lineup[pl["person"]["id"]] = (
                        int(bo) // 100, pl["person"].get("fullName", "?"))
            if side_n >= 8:      # a real posted order, not a stray sub tag
                posted_sides += 1
    except (KeyError, TypeError):
        pass
    if not lineup:
        lines.append("… lineup not posted yet — MANUAL lineup check required "
                     "(5/27 rule)")
        loud = True
        return loud, lines
    for bid, bname in sorted(g["bats"], key=lambda b: b[1]):
        if bid in lineup:
            lines.append(f"✓ {bname} — batting {lineup[bid][0]}")
        elif posted_sides < 2:
            # 7/27 hotfix C: with only ONE side posted, an absent bat is
            # probably on the unposted side — defer, don't false-scratch.
            lines.append(f"… {bname} — side not posted yet, re-check due")
            loud = True
        else:
            lines.append(f"⚠ {bname} — NOT IN POSTED LINEUP "
                         "(benched/traded/scratched?) — bet is off unless "
                         "he appears")
            loud = True
    return loud, lines


# --------------------------------------------------------------------------
def _norm_name(s):
    """NFKD accent-normalized lowercase key (the Peña lesson)."""
    import unicodedata
    return unicodedata.normalize("NFKD", s or "").encode(
        "ascii", "ignore").decode().lower().strip()


def export_lineup_status(date, g, lines):
    """Merge this game's T-30 bat statuses into out/lineup_status.json.

    Display-only artifact for the dashboard's client-side badges: bats are
    ANNOTATED, never removed — the locked board table stays byte-identical
    (no-peek preserved). Absent key = still pending. Later checks overwrite
    earlier ones (a bat can go pending → in once his side posts).
    Best-effort: any failure here must never break a precheck run.
    """
    import json
    import re
    try:
        path = Path("out/lineup_status.json")
        data = {"date": date, "updated_at": None, "bats": {}}
        if path.exists():
            try:
                old = json.loads(path.read_text())
                if old.get("date") == date:      # new slate day → fresh file
                    data = old
            except Exception:
                pass
        for line in lines:
            m = re.match(r"✓\s+(.+?)\s+—\s+batting\s+(\d+)", line.strip())
            if m:
                data["bats"][_norm_name(m.group(1))] = {
                    "st": "in", "slot": int(m.group(2)), "name": m.group(1)}
                continue
            m = re.match(r"⚠\s+(.+?)\s+—\s+NOT IN POSTED LINEUP", line.strip())
            if m:
                data["bats"][_norm_name(m.group(1))] = {
                    "st": "out", "slot": None, "name": m.group(1)}
        data["updated_at"] = datetime.now(timezone.utc).isoformat(
            timespec="seconds")
        path.write_text(json.dumps(data, ensure_ascii=False))
    except Exception as e:
        print(f"  (lineup_status.json export skipped: {e})")


# --------------------------------------------------------------------------
def due_checks(con, date, board, sidx, now, force=None):
    """Yield (check_id, kind, pk) for checks due and not yet logged.

    A T30 whose logged findings show an incomplete lineup ('not posted' /
    'side not posted') does NOT count as done — it re-fires each cron run
    until lineups are fully posted or first pitch, whichever comes first.
    """
    done = set()
    for cid, findings in con.execute(
        "SELECT check_id, findings FROM precheck_log WHERE slate_date=?",
        (date,),
    ):
        if cid.startswith("T30") and ("not posted" in (findings or "")):
            continue   # incomplete — eligible to re-run
        done.add(cid)
    if force:
        kind, _, pk = force.partition(":")
        cid = force.upper() if kind.lower() == "t90" else f"T30:{pk}"
        yield (cid, kind.lower(), int(pk) if pk else None)
        return
    starts = [sidx[pk]["start"] for pk in board if pk in sidx]
    if not starts:
        return
    first = min(starts)
    if "T90" not in done and first - T90_LEAD <= now < first:
        yield ("T90", "t90", None)
    for pk in board:
        s = sidx.get(pk)
        if not s:
            continue
        cid = f"T30:{pk}"
        if cid not in done and s["start"] - T30_LEAD <= now < s["start"]:
            yield (cid, "t30", pk)



EARLY_LEAD = timedelta(hours=4)   # start quiet lineup polling T-240


def quiet_lineup_pass(date, board, sidx, now):
    """Badge feed for early-posted lineups (no loud machinery).

    Teams post lineups 2-4h before first pitch; the loud T-30 gate stays
    where it is (closest-to-final truth, 5/27 rule), but any game inside
    EARLY_LEAD with a posted lineup gets its statuses exported to
    lineup_status.json so dashboard badges appear hours earlier.
    No issues, no md blocks, no precheck_log rows — display feed only.
    Skips games whose boarded bats all have a status already (cheap poll).
    """
    import json
    try:
        known = {}
        p = Path("out/lineup_status.json")
        if p.exists():
            j = json.loads(p.read_text())
            if j.get("date") == date:
                known = j.get("bats", {})
        for pk, g in board.items():
            s = sidx.get(pk)
            if not s:
                continue
            start = s["start"]
            if not (start - EARLY_LEAD <= now < start - T30_LEAD):
                continue   # T-30 window handles the rest, loudly
            if all(_norm_name(bn) in known for _, bn in g["bats"]):
                continue   # every boarded bat already badged for this game
            try:
                box = fetch_boxscore(pk)
            except Exception:
                continue
            lines = []
            lineup = {}
            try:
                for side in ("home", "away"):
                    side_n = 0
                    for key, pl in (box["teams"][side]["players"] or {}).items():
                        bo = pl.get("battingOrder")
                        if bo:
                            side_n += 1
                            if str(bo).endswith("00"):
                                lineup[pl["person"]["id"]] = (
                                    int(bo) // 100,
                                    pl["person"].get("fullName", "?"))
            except (KeyError, TypeError):
                continue
            if not lineup:
                continue   # nothing posted yet — stay quiet
            for bid, bname in g["bats"]:
                if bid in lineup:
                    lines.append(f"✓ {bname} — batting {lineup[bid][0]}")
                else:
                    lines.append(f"⚠ {bname} — NOT IN POSTED LINEUP (early)")
            if lines:
                export_lineup_status(date, g, lines)
                print(f"  [early] {g['label']}: lineup posted — "
                      f"{len(lines)} bats badged")
    except Exception as e:
        print(f"  (quiet lineup pass skipped: {e})")



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="hrapp.db")
    ap.add_argument("--date", default=None, help="slate date (ET), default today")
    ap.add_argument("--force", default=None,
                    help="'t90' or 't30:<gamePk>' — run regardless of window")
    args = ap.parse_args()

    date = args.date or datetime.now(ET).strftime("%Y-%m-%d")
    now = datetime.now(timezone.utc)
    con = sqlite3.connect(args.db)
    con.execute(DDL)

    board = board_games(con, date)
    if not board:
        print(f"[precheck] no locked board for {date} — nothing to verify")
        return 0
    arms = scored_arms(con, date)
    sidx = sched_index(fetch_schedule(date))

    quiet_lineup_pass(date, board, sidx, now)

    todo = list(due_checks(con, date, board, sidx, now, force=args.force))
    if not todo:
        print(f"[precheck] {date}: no checks due at "
              f"{now.strftime('%H:%M')}Z — ok")
        return 0

    any_loud, report_blocks, loud_blocks = False, [], []
    for cid, kind, pk in todo:
        if kind == "t90":
            loud, lines = check_t90(board, arms, sidx)
            hdr = f"## T-90 slate sweep — {now.strftime('%H:%M')}Z"
        else:
            g = board[pk]
            try:
                box = fetch_boxscore(pk)
            except Exception:
                box = {}   # check_t30 degrades to loud 'manual lineup check'
            loud, lines = check_t30(pk, g, arms, sidx, box)
            export_lineup_status(date, g, lines)
            hdr = (f"## T-30 — {g['label']} (pk {pk}) — "
                   f"{now.strftime('%H:%M')}Z")
        block = hdr + "\n" + "\n".join(f"- {ln}" for ln in lines)
        report_blocks.append(block)
        if loud:
            loud_blocks.append(block)
            any_loud = True
        con.execute(
            """INSERT OR REPLACE INTO precheck_log VALUES (?,?,?,?,?)""",
            (date, cid, int(loud), "\n".join(lines),
             now.isoformat(timespec="seconds")))
    con.commit()

    outdir = Path("out/prechecks")
    outdir.mkdir(parents=True, exist_ok=True)
    rp = outdir / f"precheck_{date}.md"
    header = (f"# Prechecks — {date}\n_automated 5/27 sweeps · human gate "
              "still mandatory (PropFinder + MLB app) · research log_\n")
    existing = rp.read_text() if rp.exists() else header
    rp.write_text(existing + "\n" + "\n\n".join(report_blocks) + "\n")

    if any_loud:
        (outdir / "_latest_loud.md").write_text(
            f"Slate {date} — precheck findings needing eyes:\n\n"
            + "\n\n".join(loud_blocks)
            + "\n\n_Automated sweep. Verify via PropFinder + MLB app "
              "(5/27 rule). Board is locked and unchanged._\n")

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"loud={'true' if any_loud else 'false'}\n")
            f.write(f"slate={date}\n")

    print("\n\n".join(report_blocks))
    return 2 if any_loud else 0


if __name__ == "__main__":
    sys.exit(main())
