# Season Ledger — HR board
_generated 2026-08-06T14:59:43+00:00 · research log, not advice · misses reported as loudly as hits_

| # | Date | Board | Hits | Hit% | Capture | Pen share | Cfg | Src | Note |
|---|------|------:|-----:|-----:|--------:|----------:|-----|-----|------|
| 1 | 2026-07-23 | 8 | 0 | 0.0% | — | — | ? | manual | pre-board format (promotes 0/8) |
| 2 | 2026-07-24 | — | — | — | PENDING | — | ? | manual | LOCKED, UNGRADED — backfill pending |
| 3 | 2026-07-25 | 130 | 4 | 3.1% | — | — | 1.3 | manual | old grading source, least trustworthy |
| 4 | 2026-07-26 | 108 | 15 | 13.9% | 16/40 (40%) | — | 1.4 | manual | capture 40% (16/40 approx from pct) |
| 5 | 2026-07-27 | 103 | 6 | 5.8% | 6/28 (21%) | 36% | 1.4 | db |  |
| 6 | 2026-07-28 | 149 | 12 | 8.1% | 12/29 (41%) | — | 1.5.2 | manual | graded pre-hr_finals; 12/29 capture verified |
| 7 | 2026-07-29 | 70 | 9 | 12.9% | 9/23 (39%) | 35% | 1.6.0 | db |  |
| 8 | 2026-07-30 | 105 | 16 | 15.2% | 17/18 (94%) | 39% | 1.6.0 | db |  |
| 9 | 2026-07-31 | 142 | 22 | 15.5% | 22/37 (59%) | 30% | 1.6.0 | db |  |
| 10 | 2026-08-01 | 134 | 9 | 6.7% | 10/35 (29%) | 34% | 1.6.0 | db |  |
| 11 | 2026-08-02 | 127 | 13 | 10.2% | 13/30 (43%) | 50% | 1.6.0 | db |  |
| 12 | 2026-08-03 | 81 | 13 | 16.0% | 16/30 (53%) | 50% | 1.6.0 | db |  |
| 13 | 2026-08-04 | 139 | 16 | 11.5% | 17/30 (57%) | 40% | 1.6.2 | db |  |
| 14 | 2026-08-05 | 152 | 15 | 9.9% | 16/30 (53%) | 43% | 1.6.2 | db |  |

## Aggregates
- **Season:** 150/1448 = 10.4% across 13 graded slates
- **Last 5 graded:** 66/633 = 10.4%
- **Current config (v1.6.2-2026-08-03):** 31/291 = 10.7% across 2 slates — 3 more for clean 5-slate sample
- **Pen share of slate HRs (db-graded):** 39%

_Typical board breakeven ~15–18%. Season rate below that is not edge;_
_the number above is the honest one._

## Appearance-adjusted view (statsapi boxscore)
_Full board is the research/capture identity and stays the headline._
_Active = boarded bats with >=1 PA; dead = scratched/benched/never batted._

| Date | Board | Hit% | Active | Active hit% | Dead bats | Board PA | HR/PA |
|------|------:|-----:|-------:|------------:|----------:|---------:|------:|
| 2026-07-27 | 103 | 5.8% | 84 | 7.1% | 19 | 340 | 1.76% |
| 2026-07-29 | 70 | 12.9% | 68 | 13.2% | 2 | 284 | 3.17% |
| 2026-07-30 | 105 | 15.2% | 93 | 17.2% | 12 | 367 | 4.36% |
| 2026-07-31 | 142 | 15.5% | 124 | 17.7% | 18 | 485 | 4.54% |
| 2026-08-01 | 134 | 6.7% | 117 | 7.7% | 17 | 471 | 1.91% |
| 2026-08-02 | 127 | 10.2% | 120 | 10.8% | 7 | 485 | 2.68% |
| 2026-08-03 | 81 | 16.0% | 68 | 19.1% | 13 | 281 | 4.63% |
| 2026-08-04 | 139 | 11.5% | 118 | 13.6% | 21 | 452 | 3.54% |
| 2026-08-05 | 152 | 9.9% | 128 | 11.7% | 24 | 479 | 3.13% |

- **Full board:** 119/1053 = 11.3%
- **Active board:** 119/920 = 12.9% (133 dead bats removed, 13% of locks)
- **HR per PA (active):** 119/3644 = 3.27% vs league ~3.4%

### Board depth (slate-wide hr_prob rank, deduped)
| Bucket | Hits | Rate |
|--------|-----:|-----:|
| top-10 | 14/90 | 15.6% |
| 11-30 | 25/180 | 13.9% |
| 31-60 | 37/270 | 13.7% |
| 61+ | 43/513 | 8.4% |
| **top-60 pooled** | 76/540 | 14.1% |
_Breakeven ~15-18%. Selection-layer candidate cut lines._

## Shadow lanes (auto-graded, annotate-only)
_per-start figures are not IP-adjusted; sample spans multiple config versions; no ranking weight moves without 5-6 slate validation + config bump_

### Board lanes
| lane | hits | rate |
|---|---|---|
| one-pitch | 23/135 | 17.0% |
| standard | 63/573 | 11.0% |
| watch | 11/106 | 10.4% |
| thin | 23/244 | 9.4% |

### Whiff overlay (starter HRs allowed / start)
| whiff | starts | HRs | per-start |
|---|---|---|---|
| >=30% | 47 | 29 | 0.62 |
| 28-30% | 69 | 52 | 0.75 |
| 26-28% | 79 | 56 | 0.71 |
| 20-26% | 290 | 214 | 0.74 |
| <=20% | 107 | 70 | 0.65 |

### Verdict gate (starter HRs allowed / start)
| verdict | starts | HRs | per-start |
|---|---|---|---|
| FADE | 45 | 58 | 1.29 |
| ONE-PITCH | 122 | 94 | 0.77 |
| TARGET | 245 | 180 | 0.73 |
| TARGET-THIN | 81 | 45 | 0.56 |
| ONE-PITCH-THIN | 61 | 29 | 0.48 |
| NO-READ | 38 | 15 | 0.39 |

### Gate 2.5 form (starter HRs allowed / start)
| form flag | starts | HRs | per-start |
|---|---|---|---|
| — | 210 | 173 | 0.82 |
| EMERGING | 12 | 12 | 1.00 |
| DECLINING-TARGET | 151 | 104 | 0.69 |
| CONFIRMED? | 73 | 53 | 0.73 |
| CONFIRMED | 113 | 73 | 0.65 |
| N/A | 33 | 6 | 0.18 |

### Pen-edge (per pen-slate)
| edge tier | pens | pen HRs | boarded hits | HRs/pen |
|---|---|---|---|---|
| edge 0-2 | 77 | 22 | 15 | 0.29 |
| edge 3-5 | 108 | 37 | 14 | 0.34 |
| edge>=6 | 85 | 31 | 21 | 0.36 |

### DTP batter profiles (boarded-bat hit rate)
| profile | hits | rate |
|---|---|---|
| INSANE | 4/32 | 12.5% |
| ELITE | 6/61 | 9.8% |
| FLYBALL | 9/66 | 13.6% |
| LINEDRIVE | 4/46 | 8.7% |
| ANY PROFILE | 23/205 | 11.2% |
| (untagged) | 96/848 | 11.3% |

### L15 heat tags (boarded-bat hit rate)
| tag | hits | rate |
|---|---|---|
| LOUD | 37/301 | 12.3% |
| SUSTAINED | 20/164 | 12.2% |
| HEATING | 20/155 | 12.9% |
| WARM | 7/58 | 12.1% |
| NEAR | 32/279 | 11.5% |
| COOLING | 26/247 | 10.5% |
| QUIET | 8/62 | 12.9% |

### Adjusted-read layer: 0 slate(s) logged — log rankings into adjusted_reads to start the tally
