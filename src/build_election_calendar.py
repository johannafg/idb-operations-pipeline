#!/usr/bin/env python3
"""
Pipeline Fixes Punch List, item 12 (Specification C only, election-year half):
expands a user-supplied national election-date list into the (country_iso3,
year, is_election_year) panel-ready table Spec C's administrative/political-
calendar control needs (Methodology_Approval_Rate_Extensive_Margin.tex,
threat: "Administrative and political approval calendar").

DELIBERATELY does NOT ship with hand-filled election dates for the ~25
countries in this corpus. Compiling 25 countries x ~28 years (1998-2026) of
presidential/legislative election dates accurately from memory is not
something to do with confidence -- a wrong election year baked silently into
a control variable is worse than an honestly incomplete one. Use a real
source instead: IDEA's Voter Turnout Database (idea.int/data-tools/data/voter-turnout-database),
the NELDA dataset (National Elections across Democracy and Autocracy), or
IFES's Election Guide (electionguide.org) all have per-country election-date
tables you can export and feed into this script.

Input format expected (--elections-csv): a CSV with columns
country_iso3, election_date (YYYY-MM-DD), election_type (e.g. "presidential",
"legislative") -- one row per election. See example_elections.csv in this
folder for the exact format (2 illustrative, real, easily-verifiable rows
only -- NOT a usable dataset on its own).

Usage:
    python3 build_election_calendar.py --elections-csv my_elections.csv \
        --out ../output/election_calendar.csv
"""
import argparse
import sys
from pathlib import Path

import pandas as pd


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--elections-csv", required=True,
                     help="country_iso3,election_date,election_type -- see module docstring for sources")
    ap.add_argument("--start-year", type=int, default=1998)
    ap.add_argument("--end-year", type=int, default=2026)
    ap.add_argument("--out", default="../output/election_calendar.csv")
    args = ap.parse_args()

    if not Path(args.elections_csv).exists():
        print(f"[error] {args.elections_csv} not found. This script deliberately does not ship "
              f"with election-date data -- see the module docstring for real sources (IDEA, "
              f"NELDA, IFES Election Guide) to build that file from.", file=sys.stderr)
        sys.exit(1)

    elections = pd.read_csv(args.elections_csv)
    required = {"country_iso3", "election_date", "election_type"}
    if not required.issubset(elections.columns):
        print(f"[error] {args.elections_csv} is missing required columns: "
              f"{required - set(elections.columns)}", file=sys.stderr)
        sys.exit(1)

    elections["election_date"] = pd.to_datetime(elections["election_date"], errors="coerce")
    elections = elections.dropna(subset=["election_date"])
    elections["year"] = elections["election_date"].dt.year

    countries = sorted(elections["country_iso3"].unique())
    years = list(range(args.start_year, args.end_year + 1))
    grid = pd.MultiIndex.from_product([countries, years], names=["country_iso3", "year"]).to_frame(index=False)

    election_years = elections[["country_iso3", "year", "election_type"]].drop_duplicates()
    grid = grid.merge(
        election_years.groupby(["country_iso3", "year"])["election_type"]
                      .apply(lambda s: ";".join(sorted(s.unique()))).reset_index(),
        on=["country_iso3", "year"], how="left"
    )
    grid["is_election_year"] = grid["election_type"].notna()

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    grid.to_csv(args.out, index=False)
    print(f"Wrote {args.out}: {len(grid)} country-year rows "
          f"({len(countries)} countries from {args.elections_csv}, {args.start_year}-{args.end_year})")
    print(f"election years flagged: {grid['is_election_year'].sum()}")
    missing_countries = set(pd.read_csv(args.elections_csv)["country_iso3"]) if False else None
    print("\nNOTE: only countries present in your --elections-csv are covered. Cross-check the "
          "country list above against country_portfolio_shares.csv's full set before using this "
          "as a control -- a country missing here will silently read as 'never an election year' "
          "rather than 'unknown', which is a real risk if your source file is incomplete.")


if __name__ == "__main__":
    main()
