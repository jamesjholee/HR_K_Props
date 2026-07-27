"""db.py — SQLite persistence. Predictions lock at insert time (no-peek rule)."""
import sqlite3, json, os
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hrapp.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS slates(
  slate_date TEXT PRIMARY KEY, config_version TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS pitcher_verdicts(
  slate_date TEXT, pitcher_id INTEGER, pitcher_name TEXT, verdict TEXT,
  vector TEXT, whiff REAL, bbe INTEGER, notes TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS hr_board(
  slate_date TEXT, game TEXT, batter_id INTEGER, batter_name TEXT,
  batting_order INTEGER, hr_prob REAL, breakeven TEXT, engine_rank INTEGER,
  final_rank INTEGER, l15_flag TEXT, human_override TEXT, lane TEXT,
  locked_at TEXT);
CREATE TABLE IF NOT EXISTS k_paper(
  slate_date TEXT, pitcher_id INTEGER, pitcher_name TEXT, proj_k REAL,
  lo INTEGER, hi INTEGER, arsenal_whiff REAL, opp_k_rank INTEGER,
  locked_at TEXT, actual_k INTEGER, graded_at TEXT);
CREATE TABLE IF NOT EXISTS results(
  slate_date TEXT, batter_name TEXT, team TEXT, pitcher_name TEXT,
  pitch_type TEXT, off_reliever INTEGER, was_boarded INTEGER, lane TEXT,
  entered_at TEXT);
CREATE TABLE IF NOT EXISTS alerts(
  slate_date TEXT, level TEXT, message TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS pitcher_form(
  slate_date TEXT, pitcher_id INTEGER, pitcher_name TEXT, season_verdict TEXT,
  form_flag TEXT, detail TEXT, bbe INTEGER, hr INTEGER, hh_pct REAL,
  ff_velo REAL, created_at TEXT);
CREATE TABLE IF NOT EXISTS odds_lock(
  slate_date TEXT, batter_id INTEGER, batter_name TEXT, book TEXT,
  american INTEGER, model_prob REAL, ev REAL, locked_at TEXT);
"""

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def conn():
    c = sqlite3.connect(DB_PATH)
    c.executescript(SCHEMA)
    return c

def open_slate(slate_date, config_version):
    c = conn()
    c.execute("INSERT OR IGNORE INTO slates VALUES(?,?,?)",
              (slate_date, config_version, now()))
    c.commit(); c.close()

def log_verdict(slate_date, pid, name, verdict, vector, whiff, bbe, notes=""):
    c = conn()
    c.execute("INSERT INTO pitcher_verdicts VALUES(?,?,?,?,?,?,?,?,?)",
              (slate_date, pid, name, verdict, ",".join(vector or []),
               whiff, bbe, notes, now()))
    c.commit(); c.close()

def lock_board_row(slate_date, game, bid, name, order, prob, be,
                   engine_rank, final_rank, l15_flag="", override="", lane="standard"):
    c = conn()
    c.execute("INSERT INTO hr_board VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (slate_date, game, bid, name, order, prob, be, engine_rank,
               final_rank, l15_flag, override, lane, now()))
    c.commit(); c.close()

def lock_k(slate_date, pid, name, proj):
    c = conn()
    c.execute("INSERT INTO k_paper(slate_date,pitcher_id,pitcher_name,proj_k,"
              "lo,hi,arsenal_whiff,opp_k_rank,locked_at) VALUES(?,?,?,?,?,?,?,?,?)",
              (slate_date, pid, name, proj["proj_k"], proj["range"][0],
               proj["range"][1], proj["arsenal_whiff"], proj["opp_k_rank"], now()))
    c.commit(); c.close()

def log_form(slate_date, pid, name, season_verdict, fm):
    """Gate 2.5 shadow ledger — one row per starter per slate."""
    s = fm.get("last3", {}) or {}
    c = conn()
    c.execute("INSERT INTO pitcher_form VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (slate_date, pid, name, season_verdict, fm.get("flag", ""),
               fm.get("detail", ""), s.get("bbe"), s.get("hr"),
               s.get("hh_pct"), s.get("ff_velo"), now()))
    c.commit(); c.close()

def lock_odds(slate_date, bid, name, book, american, prob, ev_val):
    c = conn()
    c.execute("INSERT INTO odds_lock VALUES(?,?,?,?,?,?,?,?)",
              (slate_date, bid, name, book, american, prob, ev_val, now()))
    c.commit(); c.close()

def log_alert(slate_date, level, message):
    c = conn()
    c.execute("INSERT INTO alerts VALUES(?,?,?,?)", (slate_date, level, message, now()))
    c.commit(); c.close()

def enter_result(slate_date, batter, team, pitcher, pitch, off_reliever, boarded, lane=""):
    c = conn()
    c.execute("INSERT INTO results VALUES(?,?,?,?,?,?,?,?,?)",
              (slate_date, batter, team, pitcher, pitch,
               1 if off_reliever else 0, 1 if boarded else 0, lane, now()))
    c.commit(); c.close()

def dump(slate_date):
    c = conn(); c.row_factory = sqlite3.Row
    out = {}
    for t in ("pitcher_verdicts", "hr_board", "k_paper", "alerts", "results"):
        out[t] = [dict(r) for r in
                  c.execute(f"SELECT * FROM {t} WHERE slate_date=?", (slate_date,))]
    c.close()
    return out
