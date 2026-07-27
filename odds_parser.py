"""
odds_parser.py — LOCKED parser for /MLB/v2 odds feed (hrapp v1.5)
=================================================================
Replaces the defensive scanner in odds.py. Schema verified against real
odds_v2.json 2026-07-27 (24.9MB snapshot, 13 games, 339 players).

VERIFIED SCHEMA:
  top level: {games: [...], players: [...]}
  players[i].odds = flat list of rows:
    {id, gameId, playerId, sportsbook, market, categoryMapping,
     selection, selectionLine, price (American int), points,
     isMain?, deepLink*, lastUpdated (ISO)}

THE 7/26 JUNK-PRICE ROOT CAUSE (documented, do not regress):
  1. ALT-LINES: market 'Player Home Runs' carries points=0.5 (hit a HR),
     points=1.5 (2+ HRs, +1350..+4700), points=2.5 (3+ HRs, +10000+).
     Old scanner took max price across all rows -> harvested 2+/3+ HR
     prices (+22499/+39997 class). FIX: main line ONLY.
     isMain==True maps 1:1 to points==0.5 (verified 212=212); filter on
     BOTH belt-and-suspenders.
  2. UNDERDOG IS NOT A SPORTSBOOK PRICE: pick'em payout values
     (Schwarber +124 vs market ~+190). PrizePicks absent from HR market
     entirely. NEITHER may enter best-price or EV. Kept in per-book dict
     for display/DFS reference only.

COVERAGE REALITY (7/26 snapshot): DK 71 hitters, FD 18, MGM 18 on the
main line. This is a DK-anchored feed. Board names without any real-book
row MUST surface as NO-PRICE, never fall back to junk. Manual price
verification remains a permanent human gate.
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta

ET_OFFSET = timedelta(hours=-4)  # EDT; feed gameDate is UTC

HR_MARKET = "Player Home Runs"
MAIN_POINTS = 0.5
REAL_BOOKS = {"DraftKings", "FanDuel", "BetMGM"}      # best-price/EV eligible
DISPLAY_ONLY_BOOKS = {"Underdog", "PrizePicks"}        # never in EV
STALE_SECONDS = 2 * 3600                               # flag, don't drop


def _ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def slate_game_ids(data: dict, slate_date: str) -> set[int]:
    """
    CROSS-DATE CONTAMINATION FIX (verified 2026-07-27): a single feed pull
    contained 1x 7/26 game + 12x 7/27 games. Unfiltered joins pull
    yesterday's players onto today's board (the phantom Schuemann row).
    slate_date: 'YYYY-MM-DD' in US Eastern.
    """
    ids = set()
    for g in data.get("games", []):
        local = _ts(g["gameDate"]) + ET_OFFSET
        if local.strftime("%Y-%m-%d") == slate_date:
            ids.add(g["id"])
    return ids


def lineup_player_ids(data: dict, game_ids: set[int]) -> set[int]:
    """
    LINEUP SANITY GATE. A player's odds only count if he appears in a
    batting order of a slate game. Missing/empty orders => no restriction
    from that game (early feed), so the gate never false-positives before
    lineups post.
    History note: the 7/26 "Schuemann misattribution" that motivated this
    gate turned out NOT to be a feed bug — Schuemann was traded ATH->NYY
    in Feb 2026; the row was legitimate and this gate correctly verified
    it. Keeping the gate anyway: cheap insurance against real cross-row
    bugs, and it correctly stays silent on legitimate rows.
    """
    pids = set()
    for g in data.get("games", []):
        if g["id"] not in game_ids:
            continue
        for key in ("homeBattingOrder", "visitorBattingOrder"):
            raw = g.get(key)
            # VERIFIED FORMAT: comma-separated MLBAM ID string
            # ("607679,502671,..."); empty/None before lineups post.
            if not raw:
                continue
            if isinstance(raw, str):
                items = raw.split(",")
            else:  # tolerate list form if feed ever changes
                items = raw
            for pid in items:
                if isinstance(pid, dict):
                    pid = pid.get("id")
                try:
                    pids.add(int(pid))
                except (TypeError, ValueError):
                    continue
    return pids


def parse_hr_odds(data: dict, slate_date: str | None = None) -> dict[int, dict]:
    """
    Returns {playerId: {
        'name': str,
        'books': {book: {'price': int, 'updated': str, 'stale': bool}},
        'best': {'book': str, 'price': int, 'stale': bool} | None,  # real books only
        'display_only': {book: price},                              # Underdog etc.
    }}
    Players with no real-book main-line row get best=None (NO-PRICE state).
    """
    # feed max timestamp = staleness reference
    all_times = [
        _ts(o["lastUpdated"])
        for p in data.get("players", [])
        for o in (p.get("odds") or [])
        if o.get("market") == HR_MARKET
    ]
    ref = max(all_times) if all_times else datetime.now(timezone.utc)

    valid_games: set[int] | None = None
    lineup_ids: set[int] = set()
    if slate_date:
        valid_games = slate_game_ids(data, slate_date)
        lineup_ids = lineup_player_ids(data, valid_games)

    out: dict[int, dict] = {}
    for p in data.get("players", []):
        books, display = {}, {}
        for o in (p.get("odds") or []):
            if o.get("market") != HR_MARKET:
                continue
            if valid_games is not None and o.get("gameId") not in valid_games:
                continue  # cross-date row: hard drop
            # MAIN LINE ONLY — both conditions, never regress to max-scan
            if o.get("points") != MAIN_POINTS or not o.get("isMain", False):
                continue
            book = o.get("sportsbook", "?")
            if book in DISPLAY_ONLY_BOOKS:
                display[book] = o["price"]
                continue
            if book not in REAL_BOOKS:
                continue  # unknown book: exclude until reviewed, log upstream
            stale = (ref - _ts(o["lastUpdated"])).total_seconds() > STALE_SECONDS
            prev = books.get(book)
            # no dupes observed, but keep newest if feed ever repeats
            if prev is None or o["lastUpdated"] > prev["updated"]:
                books[book] = {"price": o["price"], "updated": o["lastUpdated"],
                               "stale": stale}
        if not books and not display:
            continue
        best = None
        if books:
            bb = max(books, key=lambda b: books[b]["price"])
            best = {"book": bb, "price": books[bb]["price"],
                    "stale": books[bb]["stale"]}
        # lineup sanity gate: only meaningful once lineups have posted
        suspect = bool(lineup_ids) and p["id"] not in lineup_ids
        out[p["id"]] = {"name": p.get("fullName", "?"), "books": books,
                        "best": best, "display_only": display,
                        "lineup_verified": not suspect}
    return out


def dashboard_price(entry: dict | None) -> str:
    """Render for the board's odds column."""
    if entry is None or entry["best"] is None:
        return "NO-PRICE (verify manually)"
    b = entry["best"]
    flag = " ⚠STALE" if b["stale"] else ""
    return f"+{b['price']} {b['book']}{flag}"


if __name__ == "__main__":
    import json, sys
    path = sys.argv[1] if len(sys.argv) > 1 else "raw/odds_v2.json"
    slate = sys.argv[2] if len(sys.argv) > 2 else None  # YYYY-MM-DD (ET)
    data = json.load(open(path))
    parsed = parse_hr_odds(data, slate_date=slate)
    priced = [e for e in parsed.values() if e["best"]]
    tag = f" (slate {slate})" if slate else " (UNFILTERED — pass a date!)"
    print(f"{len(parsed)} players with HR rows; {len(priced)} priced{tag}")
    for e in sorted(priced, key=lambda x: -x["best"]["price"])[:15]:
        warn = "" if e["lineup_verified"] else "  ⚠NOT-IN-LINEUP"
        print(f"  {e['name']:<24} {dashboard_price(e)}{warn}")
