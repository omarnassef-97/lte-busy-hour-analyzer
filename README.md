# lte-busy-hour-analyzer

Per-cell, per-day busy-hour identification and PRB/UE/throughput threshold-exceedance counting for LTE cells — vectorized for large daily hourly exports.

## Problem

To spot capacity problems, network teams need to know, for every cell every day: which hour was busiest, how many hours ran hot on PRB utilization, and how many hours had congestion signals (high PRB, many active users, degraded throughput) all at once. Hourly exports for a sizeable network are large, and a row-by-row Python loop to parse and classify every reading doesn't scale.

## Approach

- **Vectorized parsing.** Source values like `"90%"`, `"3 Mbps"`, or a stray typo'd unit are cleaned and converted to numbers with vectorized pandas string operations instead of a per-row Python function call, so it stays fast on large daily exports.
- **Busy hour via `idxmax`.** For each cell/day, the busy hour is the one with the highest plausible PRB reading, found directly with `groupby().idxmax()` rather than sorting the whole frame.
- **Corrupt-value guard.** PRB readings above a configurable ceiling are excluded from busy-hour selection rather than silently accepted — a cell/day where every reading is missing or implausible is flagged in `BH_Invalid` instead of getting an arbitrary fallback hour.
- **Threshold counting.** Counts, per cell/day, how many hours exceed the PRB threshold, and how many hours meet the PRB/active-UE/throughput conditions together — plus how many hours had a usable PRB reading at all, so a rate can be computed against the right denominator.
- **Configurable everything.** PRB, active-UE and throughput thresholds, and the PRB ceiling used for busy-hour selection, are all CLI flags with sensible defaults.

## Tech stack

Python · pandas · NumPy

## How to run

```
pip install -r requirements.txt
python main.py -i your_hourly_export.csv -o daily_exceed.csv
```

Useful flags:

```
--prb_threshold      PRB utilization above which an hour counts as congested (default 90)
--ues_threshold       Average active UEs above which an hour counts as loaded (default 1.5)
--ip_thp_threshold    IP throughput below which an hour counts as degraded (default 5000; match your data's units)
--bh_max_prb          PRB values above this are treated as corrupt and excluded from busy-hour selection (default 100)
--debug               Print intermediate diagnostics
```

Input is expected to be one row per cell per hour, with at least `sitecode`, `DN`, `LNCEL name`, `PERIOD_START_TIME`, and a PRB utilization column.

## Project history

This is a rewrite of an earlier version that parsed each value with a per-row `.apply()` call and found the busy hour by sorting the whole frame and taking the first row per group. This version replaces both with vectorized pandas operations (`str` accessor parsing, `groupby().idxmax()`), which matters once the input is a full network's worth of daily hourly readings rather than a quick sample.

## Note on data

No real network data is included — point `-i` at your own hourly export.
# lte-busy-hour-analyzer
Busy-hour detection and PRB/UE/throughput threshold-exceedance counting for LTE cells, vectorized for large daily exports
