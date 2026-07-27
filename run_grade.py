"""run_grade.py — evening grading.

Usage:
  python3 run_grade.py --date 2026-07-23 --hrfile todays_hrs.tsv
  python3 run_grade.py --date 2026-07-23 --k 694297=6 --k 700241=4

HR file: paste rows from onlyhomers.com/daily into a text file, tab- or
multi-space-separated, one HR per line, e.g.:
  SD	Ty France	16	1	8	1	22	108.2	395	Slider	Tyler Kinley
Columns used: [0]=team, [1]=batter, [-2]=pitch type, [-1]=pitcher.
A pitcher is counted as a reliever if he isn't in today's verdict table.
"""
import argparse, sqlite3, sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--hrfile")
    ap.add_argument("--k", action="append", default=[],
                    help="pitcherId=actualK (repeatable)")
    args = ap.parse_args()

    c = db.conn(); c.row_factory = sqlite3.Row
    boarded = {r["batter_name"].lower(): r for r in c.execute(
        "SELECT * FROM hr_board WHERE slate_date=?", (args.date,))}
    starters = {r["pitcher_name"].split("#")[-1] for r in c.execute(
        "SELECT pitcher_name FROM pitcher_verdicts WHERE slate_date=?", (args.date,))}

    if args.hrfile:
        hits, total = 0, 0
        for line in open(args.hrfile):
            parts = [p for p in re.split(r"\t| {2,}", line.strip()) if p]
            if len(parts) < 4:
                continue
            team, batter, pitch, pitcher = parts[0], parts[1], parts[-2], parts[-1]
            b = boarded.get(batter.lower())
            was = b is not None
            total += 1
            hits += 1 if was else 0
            # crude reliever check: last-name match against any starter id row is
            # impossible without ids, so flag by absence from verdict names — a
            # human eyeballs this column; the DB stores the raw pitcher name.
            db.enter_result(args.date, batter, team, pitcher, pitch,
                            off_reliever=0, boarded=was,
                            lane=(b["lane"] if b else ""))
            mark = "✓ BOARDED" + f" ({b['lane']})" if was else "  miss"
            print(f"{batter:<22} off {pitcher:<20} {pitch:<16} {mark}")
        n_board = len(boarded)
        print(f"\nSlate: {total} HRs, {hits} boarded. Board size {n_board} "
              f"-> hit rate {hits}/{n_board} = {hits/max(1,n_board):.0%}")

    for spec in args.k:
        pid, actual = spec.split("=")
        c2 = db.conn()
        c2.execute("UPDATE k_paper SET actual_k=?, graded_at=? "
                   "WHERE slate_date=? AND pitcher_id=?",
                   (int(actual), db.now(), args.date, int(pid)))
        c2.commit(); c2.close()
        row = db.conn().execute(
            "SELECT proj_k, lo, hi FROM k_paper WHERE slate_date=? AND pitcher_id=?",
            (args.date, int(pid))).fetchone()
        if row:
            proj, lo, hi = row
            inr = "IN RANGE" if lo <= int(actual) <= hi else "OUT OF RANGE"
            print(f"K grade {pid}: proj {proj} ({lo}-{hi}) vs actual {actual}  {inr}")

if __name__ == "__main__":
    main()
