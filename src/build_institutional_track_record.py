#!/usr/bin/env python3
"""
Pipeline Fixes Punch List, item 8: derives institutional-capacity proxies --
a country's (and, where matchable, an executing agency's) prior IDB
operation count and historical average disbursement duration as of each
project's own approval date -- the covariate both methodology notes propose
as a control for the endogenous-exposure-shares threat (Section on
endogenous exposure shares, Methodology_Hazard_LP_ShiftShare.tex).

Computed strictly "as of approval": for project P approved in month T, only
OTHER projects with an earlier approval_date count toward P's prior_ops_*
features. A project never sees its own future, and never sees itself.
country_prior_ops_count is available for every project with a known
approval_date and country_iso3; country_prior_avg_duration_months is only
computable from PRIOR projects that had already reached a resolved
disbursed_complete state by T (an unresolved or still-active prior project
contributes to the count but not to the duration average, since its true
duration isn't known yet either).

executing_agency matching is looser than country matching -- data.xlsx's
Executing Agency field is free text, not a coded identifier, so
agency_prior_ops_count is a same-string match, undercounting cases where the
same real agency appears under slightly different spellings across projects.
Treat the agency-level features as noisier than the country-level ones.

Usage:
    python3 build_institutional_track_record.py \
        --panel ../output/microdata_panel_latest.xlsx \
        --panel-summary ../output/project_month_panel_summary.csv \
        --out ../output/institutional_track_record.csv
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

from opnum import LOAN_PATTERN


def load_panel(path: str) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        df = pd.read_excel(p, sheet_name="Microdata Panel")
    else:
        df = pd.read_csv(p, dtype=str, keep_default_na=False)
    df = df.replace({"not stated": None, "no source match": None, "": None})
    op_re = LOAN_PATTERN   # both numbering schemes -- see opnum.py
    df = df[df["operation_number"].astype(str).str.match(op_re, na=False)]
    return df


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--panel", default="../output/microdata_panel_latest.xlsx")
    ap.add_argument("--panel-summary", default="../output/project_month_panel_summary.csv",
                     help="project_month_panel_summary.csv from build_project_month_panel.py, "
                          "used for realized disbursement duration (n_months) of resolved projects")
    ap.add_argument("--out", default="../output/institutional_track_record.csv")
    args = ap.parse_args()

    panel = load_panel(args.panel)
    panel["approval_date"] = pd.to_datetime(panel["approval_date"], errors="coerce")
    panel = panel.dropna(subset=["approval_date"]).copy()

    if Path(args.panel_summary).exists():
        summ = pd.read_csv(args.panel_summary)
        summ = summ[summ["status"] == "ok"]
        resolved = summ[summ["exit_reason"] == "disbursed_complete"][["operation_number", "n_months"]]
        panel = panel.merge(resolved, on="operation_number", how="left")
    else:
        print(f"[warn] {args.panel_summary} not found -- proceeding without realized-duration "
              f"features (prior_ops_count will still be computed, but not "
              f"*_avg_duration_months). Run build_project_month_panel.py first for full output.",
              file=sys.stderr)
        panel["n_months"] = None

    panel = panel.sort_values("approval_date").reset_index(drop=True)

    country_col = "country_iso3" if "country_iso3" in panel.columns else None
    agency_col = "executing_agency" if "executing_agency" in panel.columns else None

    results = []
    for i, row in panel.iterrows():
        op = row["operation_number"]
        t = row["approval_date"]
        prior = panel[panel["approval_date"] < t]

        rec = {"operation_number": op}

        if country_col and pd.notna(row.get(country_col)):
            c_prior = prior[prior[country_col] == row[country_col]]
            rec["country_prior_ops_count"] = len(c_prior)
            durations = c_prior["n_months"].dropna()
            rec["country_prior_avg_duration_months"] = round(durations.mean(), 1) if len(durations) else None
            rec["country_prior_n_with_known_duration"] = len(durations)
        else:
            rec["country_prior_ops_count"] = None
            rec["country_prior_avg_duration_months"] = None
            rec["country_prior_n_with_known_duration"] = None

        if agency_col and pd.notna(row.get(agency_col)):
            a_prior = prior[prior[agency_col] == row[agency_col]]
            rec["agency_prior_ops_count"] = len(a_prior)
            a_durations = a_prior["n_months"].dropna()
            rec["agency_prior_avg_duration_months"] = round(a_durations.mean(), 1) if len(a_durations) else None
        else:
            rec["agency_prior_ops_count"] = None
            rec["agency_prior_avg_duration_months"] = None

        results.append(rec)

    out_df = pd.DataFrame(results)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(args.out, index=False)

    print(f"Wrote {args.out}: {len(out_df)} project(s)")
    print(f"country_prior_ops_count populated: {out_df['country_prior_ops_count'].notna().sum()}/{len(out_df)}")
    print(f"country_prior_avg_duration_months populated: "
          f"{out_df['country_prior_avg_duration_months'].notna().sum()}/{len(out_df)} "
          f"(needs at least one PRIOR, resolved-complete project in the same country)")
    print(f"agency_prior_ops_count populated: {out_df['agency_prior_ops_count'].notna().sum()}/{len(out_df)}")


if __name__ == "__main__":
    main()
