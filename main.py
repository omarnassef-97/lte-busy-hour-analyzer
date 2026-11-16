#!/usr/bin/env python3
"""
Per cell, per day:
  - identify the Busy Hour (highest PRB utilisation among plausible values)
  - count hours where PRB exceeds the threshold
  - count hours where PRB, active UEs and IP throughput conditions hold together

Input is expected to be one row per cell per hour.
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# --- source column names -----------------------------------------------------
PRB_COL = 'E-UTRAN Avg PRB usage per TTI DL'
UES_COL = 'Avg act UEs DL'
IP_COL = 'E-UTRAN avg IP sched thp DL, QCI8'
TIME_COL = 'PERIOD_START_TIME'

KEYS = ['sitecode', 'DN', 'Date', 'LNCEL name']
REQUIRED = ['sitecode', 'DN', 'LNCEL name', TIME_COL, PRB_COL]


def to_numeric_series(s: pd.Series) -> pd.Series:
    """Convert a column like '90%', '3 Mbps', ' 12.5 ' to floats; NaN if not
    convertible. Vectorised — no per-cell Python call."""
    s = s.astype(str).str.strip()
    s = s.replace({'nan': np.nan, 'None': np.nan, '': np.nan})
    s = s.str.replace('%', '', regex=False)
    s = s.str.replace('Mbps', '', regex=False)
    s = s.str.replace('Mpbs', '', regex=False)   # common typo in Nokia exports
    s = s.str.replace(',', '', regex=False)
    extracted = s.str.extract(r'([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)', expand=False)
    return pd.to_numeric(extracted, errors='coerce')


def warn_on_mixed_units(raw: pd.Series, col_name: str) -> None:
    """The numeric parser strips unit suffixes, so 5 Mbps and 5 kbps both become
    5.0. Flag it rather than silently comparing incompatible numbers."""
    text = raw.astype(str)
    has_mbps = text.str.contains('Mbps|Mpbs', case=False, na=False).any()
    if has_mbps:
        print(
            f"Warning: {col_name} contains 'Mbps' unit suffixes, which are stripped "
            f"during parsing. Make sure --ip_thp_threshold is expressed in the same "
            f"unit as the underlying numbers.",
            file=sys.stderr,
        )


def parse_args():
    p = argparse.ArgumentParser(
        description="Per cell/day: busy hour, PRB exceed hours, and hours meeting "
                    "all three conditions (one row per hour expected).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument('-i', '--input', required=True, help="Input CSV")
    p.add_argument('-o', '--out', default='daily_exceed.csv', help="Output CSV")
    p.add_argument('--prb_threshold', type=float, default=90.0,
                   help="PRB utilisation above which an hour counts as congested")
    p.add_argument('--ues_threshold', type=float, default=1.5,
                   help="Average active UEs above which an hour counts as loaded")
    p.add_argument('--ip_thp_threshold', type=float, default=5000.0,
                   help="IP throughput below which an hour counts as degraded "
                        "(must match the units of your data)")
    p.add_argument('--bh_max_prb', type=float, default=100.0,
                   help="PRB values above this are treated as corrupt and excluded "
                        "from busy-hour selection")
    p.add_argument('--debug', action='store_true',
                   help="Print intermediate diagnostics")
    return p.parse_args()


def main():
    args = parse_args()

    inp = Path(args.input)
    if not inp.exists():
        raise FileNotFoundError(f"Input file not found: {inp}")

    df = pd.read_csv(inp, low_memory=False)

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise KeyError(f"Missing required columns: {missing}")

    # --- timestamps ----------------------------------------------------------
    df[TIME_COL] = pd.to_datetime(df[TIME_COL], format='mixed', errors='coerce')
    n_bad_time = int(df[TIME_COL].isna().sum())
    if n_bad_time:
        print(f"Warning: dropping {n_bad_time} rows with unparseable {TIME_COL}.",
              file=sys.stderr)
        df = df.dropna(subset=[TIME_COL])

    if df.empty:
        raise ValueError("No rows left after timestamp parsing.")

    df['HourBucket'] = df[TIME_COL].dt.floor('h')   # identity if already hourly
    df['Date'] = df['HourBucket'].dt.date

    # --- numeric conversions -------------------------------------------------
    # warn_on_mixed_units(df[IP_COL], IP_COL)
    df['PRB_num'] = to_numeric_series(df[PRB_COL])
    df['UEs_num'] = to_numeric_series(df[UES_COL])
    df['IP_num'] = to_numeric_series(df[IP_COL])

    # --- grouping key sanity -------------------------------------------------
    null_keys = df[KEYS].isna().sum()
    if null_keys.any():
        print("Warning: null values in grouping keys — those rows form their own "
              f"groups:\n{null_keys[null_keys > 0].to_string()}", file=sys.stderr)

    if args.debug:
        print("========== DEBUG: parsing ==========")
        print(f"Total rows: {len(df)}")
        print(f"PRB parseable: {df['PRB_num'].notna().sum()}")
        print(f"UEs parseable: {df['UEs_num'].notna().sum()}")
        print(f"IP parseable:  {df['IP_num'].notna().sum()}")
        print(f"PRB > {args.prb_threshold}: {(df['PRB_num'] > args.prb_threshold).sum()}")
        print(f"PRB max: {df['PRB_num'].max()}")
        print(f"PRB > {args.bh_max_prb} (treated as corrupt): "
              f"{(df['PRB_num'] > args.bh_max_prb).sum()}")

    # --- busy hour -----------------------------------------------------------
    # Per cell per day, the hour with the highest PRB among plausible values.
    # Ties resolve to the first occurrence, i.e. the earliest hour of the day.
    # Groups whose PRB values are all corrupt or all NaN get no busy hour at all
    # rather than an arbitrary fallback — they are flagged in BH_Invalid instead.
    df_bh_src = df.dropna(subset=['PRB_num'])
    plausible = df_bh_src[df_bh_src['PRB_num'] <= args.bh_max_prb]

    idx_bh = plausible.groupby(KEYS, dropna=False)['PRB_num'].idxmax()

    bh = (
        df_bh_src.loc[idx_bh.values]
        [KEYS + ['HourBucket', 'PRB_num']]
        .rename(columns={
            'HourBucket': 'BH_Hour',
            'PRB_num': 'BH_PRB',
        })
        .reset_index(drop=True)
    )

    # --- per-hour condition flags -------------------------------------------
    # NaN comparisons already evaluate to False, so no fillna is needed.
    df['PRB_ok'] = df['PRB_num'] > args.prb_threshold
    df['UEs_ok'] = df['UEs_num'] > args.ues_threshold
    df['IP_ok'] = df['IP_num'] < args.ip_thp_threshold
    df['AllTrueInterval'] = df['PRB_ok']

    # Hours with a usable PRB reading — the denominator for the counts below.
    df['PRB_avail'] = df['PRB_num'].notna()

    if args.debug:
        print("\n========== DEBUG: conditions ==========")
        print(f"PRB_ok hours: {int(df['PRB_ok'].sum())}")
        print(f"UEs_ok hours: {int(df['UEs_ok'].sum())}")
        print(f"IP_ok hours:  {int(df['IP_ok'].sum())}")
        print(f"All three:    {int(df['AllTrueInterval'].sum())}")

    # --- daily aggregation ---------------------------------------------------
    daily = (
        df.groupby(KEYS, as_index=False, dropna=False)
        .agg(
            ExceedHours=('PRB_ok', 'sum'),            # PRB above threshold
            IntervalsAllTrue=('AllTrueInterval', 'sum'),  # all three conditions
            AvailIntervals=('PRB_avail', 'sum'),      # hours with a usable PRB value,         # daily peak active UEs
        )
    )

    for col in ['ExceedHours', 'IntervalsAllTrue', 'AvailIntervals']:
        daily[col] = daily[col].astype(int)

    daily = daily.merge(bh, on=KEYS, how='left')
    daily['BH_Invalid'] = daily['BH_PRB'].isna()

    n_invalid = int(daily['BH_Invalid'].sum())
    if n_invalid:
        print(f"Warning: {n_invalid} cell-days have no valid busy hour "
              f"(all PRB values missing or above {args.bh_max_prb}).", file=sys.stderr)

    daily = daily[[
        'sitecode',
        'LNCEL name',
        'DN',
        'Date',
        'BH_Hour',
        'BH_PRB',
        'ExceedHours',
        'IntervalsAllTrue',
        'AvailIntervals',
    ]]

    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    daily.to_csv(out_path, index=False)
    print(f"Wrote {len(daily)} rows to {out_path}")


if __name__ == '__main__':
    main()
