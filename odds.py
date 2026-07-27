"""odds.py — /MLB/v2 odds feed -> HR prop prices -> auto-EV column.  (v1.5)

v1.5: the v1.4 "defensive scanner" (_walk) is GONE. It recursively hunted
anything HR-shaped and took the longest price, which harvested alt-lines
(2+ HR at +1350..+4700, 3+ HR at +10000+) and pick'em payouts — the 7/26
junk-price incident. All parsing now goes through odds_parser.py (schema
LOCKED against a real odds_v2.json 2026-07-27):

  - main line only (points==0.5 AND isMain==True, verified 1:1)
  - real books only in best-price/EV: DK / FD / MGM
    (Underdog + PrizePicks are pick'em payouts, display-only, never EV)
  - cross-date rows dropped (feed mixes slate dates — verified)
  - explicit NO-PRICE state; staleness flag (>2h) surfaces as "⚠stale"
  - lineup sanity gate when batting orders are posted

Prices/EV remain DISPLAY + LEDGER only. They never move hr_prob(),
ranking, or promotes, and the human still verifies the live price at bet
time. Sub-+400 auto-pass rule unchanged.

Coverage reality (7/26 snapshot): DK ~71 hitters, FD/MGM ~18 each on the
main line. Expect plenty of legitimate NO-PRICE rows on a 100+ name board.
"""

import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import odds_parser
import puller


def _norm(name):
    """Accent-proof name key (the Peña lesson): NFKD-strip, letters only."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z]", "", s.lower())


def american_to_decimal(a):
    a = float(a)
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def build_book(offline=False, slate_date=None):
    """Pull the v2 feed -> {'by_id': {pid: entry}, 'by_name': {...}, 'n': int}.

    entry = {'price': int, 'book': str, 'stale': bool,
             'lineup_verified': bool, 'display_only': {book: price}}
    slate_date ('YYYY-MM-DD', ET) is REQUIRED for cross-date filtering;
    without it the feed may include adjacent days' games (logged upstream
    by run_morning's v1.4-fallback warning).
    """
    empty = {"by_id": {}, "by_name": {}, "n": 0}
    try:
        raw = puller.odds_v2(offline)
        if raw is None:
            return empty
        parsed = odds_parser.parse_hr_odds(raw, slate_date=slate_date)
        by_id, by_name = {}, {}
        n = 0
        for pid, e in parsed.items():
            if e["best"] is None:
                continue  # NO-PRICE: absent from the book, never a fallback
            entry = {
                "price": e["best"]["price"],
                "book": e["best"]["book"],
                "stale": e["best"]["stale"],
                "lineup_verified": e["lineup_verified"],
                "display_only": e["display_only"],
            }
            by_id[pid] = entry
            by_name[_norm(e["name"])] = entry
            n += 1
        return {"by_id": by_id, "by_name": by_name, "n": n}
    except Exception:
        return empty  # columns render blank, run continues, alert logs


def best_price(book, batter_id, batter_name):
    """Best REAL-book main-line HR price for one bat -> (american, book_label)
    or (None, None). book_label carries data-quality suffixes:
        '⚠stale'  price >2h older than the feed's freshest row
        '⚠lineup' player not found in any posted batting order
    Both mean: verify by hand before betting (which is the rule anyway)."""
    entry = book["by_id"].get(batter_id) or book["by_name"].get(_norm(batter_name))
    if not entry:
        return (None, None)
    label = entry["book"]
    if entry["stale"]:
        label += "⚠stale"
    if not entry["lineup_verified"]:
        label += "⚠lineup"
    return (entry["price"], label)


def ev(p_true, american):
    """EV per flat unit at p_true and the given american price."""
    if american is None or not p_true:
        return None
    return round(p_true * american_to_decimal(american) - 1.0, 3)


def fmt_price(a):
    return "" if a is None else (f"+{a}" if a > 0 else str(a))
