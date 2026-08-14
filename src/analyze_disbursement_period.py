#!/usr/bin/env python3
"""
Distribution analysis for the disbursement_period_years field extracted by
the pipeline (see schema.py / build_microdata_panel.py, field added
2026-08-13).

Why this exists: before finalizing the empirical-strategy sample-window
decision (keep the wide 1998-2026 approval window vs. restrict further --
see docs/Empirical_Strategy_Note.tex), we wanted to see the actual
distribution of stated/implied disbursement periods across the harvested
corpus. This script reads the merged panel output and reports coverage,
summary statistics, and breakdowns by lending instrument and approval-year
bucket, plus a histogram.

Usage:
    python3 analyze_disbursement_period.py
    python3 analyze_disbursement_period.py --panel ../output/microdata_panel_latest.csv
    python3 analyze_disbursement_period.py --panel ../output/microdata_panel_latest.xlsx --bins 15

If the field is not present yet (the backfill hasn't been run, or hasn't
reached any project with a stated/derivable disbursement period), this
prints a clear explanatory message and exits cleanly rather than failing --
safe to run at any point in the pipeline's progress and re-run later as the
backfill completes.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd


def load_panel(path: str) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        return pd.read_excel(p, sheet_name="Microdata Panel")
    return pd.read_csv(p, dtype=str, keep_default_na=False)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--panel",
        default="../output/microdata_panel_latest.xlsx",
        help="microdata_panel_latest.xlsx (or .csv) from build_excel_report.py / build_microdata_panel.py",
    )
    ap.add_argument("--out-dir", default="../output", help="where to save the plot and stats CSV")
    ap.add_argument("--bins", type=int, default=20)
    args = ap.parse_args()

    df = load_panel(args.panel)
    n_total = len(df)

    if "disbursement_period_years" not in df.columns:
        print(
            f"[info] Loaded {n_total} projects from {args.panel}, but "
            "'disbursement_period_years' is not present as a column yet.\n"
            "This means no project in this panel snapshot has been (re-)processed "
            "since the field was added to schema.py on 2026-08-13. Run the "
            "pipeline's normal resume command (build_microdata_panel.py, or "
            "run_all_batches.sh for the full corpus) -- entries missing this "
            "field are automatically flagged for reprocessing, no --force "
            "needed -- then re-run this script.",
            file=sys.stderr,
        )
        sys.exit(0)

    raw = df["disbursement_period_years"]
    numeric = pd.to_numeric(raw, errors="coerce")
    n_populated = int(numeric.notna().sum())
    n_missing = n_total - n_populated

    print(f"Projects total:                      {n_total}")
    print(f"disbursement_period_years populated: {n_populated} ({100 * n_populated / max(n_total,1):.1f}%)")
    print(f"missing / not stated:                {n_missing} ({100 * n_missing / max(n_total,1):.1f}%)")

    if n_populated == 0:
        print(
            "\n[info] The column exists but every value is currently null -- the "
            "backfill run hasn't completed for any project with a stated or "
            "derivable disbursement period yet. Nothing further to summarize "
            "until values are populated.",
            file=sys.stderr,
        )
        sys.exit(0)

    valid = numeric.dropna()
    stats = valid.describe(percentiles=[0.1, 0.25, 0.5, 0.75, 0.9])
    print("\nSummary statistics (years):")
    print(stats.to_string())

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stats_path = out_dir / "disbursement_period_years_stats.csv"
    stats.to_frame("value").to_csv(stats_path)
    print(f"\nSaved summary stats to {stats_path}")

    if "lending_instrument" in df.columns:
        print("\nBy lending instrument:")
        bydf = df.assign(disbursement_period_years=numeric)
        g = bydf.groupby("lending_instrument")["disbursement_period_years"].agg(["count", "mean", "median"])
        print(g.to_string())

    if "approval_date" in df.columns:
        years = pd.to_datetime(df["approval_date"], errors="coerce").dt.year
        bucket = (years // 5 * 5).astype("Int64").astype(str) + "s"
        bydf = df.assign(disbursement_period_years=numeric, approval_bucket=bucket)
        print("\nBy approval-year bucket:")
        g = bydf.groupby("approval_bucket")["disbursement_period_years"].agg(["count", "mean", "median"])
        print(g.sort_index().to_string())

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(8, 5))
        ax.hist(valid, bins=args.bins, color="#2b6cb0", edgecolor="white")
        ax.set_xlabel("Disbursement period (years)")
        ax.set_ylabel("Number of projects")
        ax.set_title(f"Distribution of disbursement_period_years (n={n_populated})")
        fig.tight_layout()
        plot_path = out_dir / "disbursement_period_years_distribution.png"
        fig.savefig(plot_path, dpi=150)
        print(f"Saved histogram to {plot_path}")
    except ImportError:
        print("[warn] matplotlib not available -- skipping histogram.", file=sys.stderr)


if __name__ == "__main__":
    main()
