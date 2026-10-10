# V4 baseline / candidate comparison

Descriptive only. Archived results are not a new historical rerun.

| Version / strategy | Trades | Win % | Expectancy R | PF R | MDD R | Sum trade return % |
|---|---:|---:|---:|---:|---:|---:|
| baseline ir1 | 3 | 33.3333 | -0.6830 | 0.0434 | 2.0491 | -1.5575 |
| baseline ir2 | 2 | 0.0000 | -1.1165 | 0.0000 | 2.2329 | -0.8137 |
| baseline ir3 | 1 | 100.0000 | 0.4426 | — | 0.0000 | 0.2729 |

Baseline data mode: `PROVISIONAL_UNREVIEWED_DATA`.
Candidate: `NOT_RUN`.
No winner selected. See JSON for every fold, excursions, concentration, costs and compatibility checks.
Cost scenarios are fixed-trade accounting counterfactuals, not ideal-execution or strategy backtests.
Zero-loss PF is null with an explicit infinite flag; it does not establish an edge.

Verdict: **inconclusive**. Six archived trades; IR1-R1 NOT_RUN; EC2 data audit and new independent baseline runs NOT_RUN.

Maximum consecutive wins/losses: 1 / 2. Year/month consistency and top/worst five contribution are in JSON.
Four fixed-trade cost scenarios: 0, 2+1, 5+1, 10+1 bps per side. At 10+1: sum trade return -3.0542%, expectancy -0.9778R, PF(R) 0.0302.
The fourth scenario is an algebraic accounting sensitivity from archived returns/R and verified $100 notional, not a new fill simulation.
No final profitability or absence-of-edge conclusion is justified. See v4_revalidation_report.md for separate one-line EC2 commands.
