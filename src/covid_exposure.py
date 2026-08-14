#!/usr/bin/env python3
"""
Generates a calendar-month COVID-exposure covariate table, for merging onto
the project-month panel by (year, month) -- see docs/Empirical_Strategy_Note.tex
and docs/Methodology_Hazard_LP_ShiftShare.tex, "Treatment of a Common,
Mechanistically Distinct Shock" / "Common pandemic-era disruption": the
recommendation there is an explicit exposure indicator entered directly in
X_it for the affected calendar months, rather than a sample-year exclusion.

This is a common (project-invariant) calendar-time control, not a
project-specific exposure share -- it does not get shift-share-interacted
with anything; it is meant to be included directly in X_it (or absorbed via
an equivalent set of month dummies) so that pandemic-era disruption is
estimated (or absorbed) rather than assumed away by restricting the
approval-year sample.

Three columns are produced, from most to least conservative:

  covid_pheic       -- 1 for calendar months inside the WHO-declared COVID-19
                        Public Health Emergency of International Concern,
                        2020-01 through 2023-05 (PHEIC declared 2020-01-30,
                        ended 2023-05-05). This is the only one of the three
                        tied to an externally verifiable date, not a judgment
                        call: https://www.who.int/europe/emergencies/situations/covid-19

  covid_acute       -- 1 for a narrower window, 2020-03 through 2021-06,
                        intended to capture the period of most severe global
                        lockdown and supply-chain disruption specifically (as
                        opposed to the full, longer official emergency
                        window). This IS a judgment call, not an official
                        designation -- treat it as a specification choice to
                        test robustness against, not a fact to defend.

  covid_intensity   -- a simple 3-level ordinal derived from the two binaries
                        above (2 = acute window, 1 = PHEIC but not acute,
                        0 = outside PHEIC entirely), provided as a convenience
                        for a single ordered regressor instead of two dummies.
                        This is a transparent construction from the two
                        binaries above, not an independent index (e.g. not
                        the Oxford COVID-19 Government Response Tracker,
                        which would be country-specific and is not what this
                        script builds -- see the module docstring note below
                        if country-level, continuous stringency is wanted
                        instead).

If country-specific, continuous stringency is wanted instead of a single
global calendar-time indicator, that requires merging in an external,
country-month stringency series (e.g. OxCGRT) and is a separate task --
deliberately out of scope here, since the methodology note's identification
argument for the shock-exposure regressor specifically relies on shocks
being global rather than country-specific (see "How the Macro Shocks Enter",
Section on shock exogeneity); a pandemic *control*, unlike the shift-share
*shock*, does not need country variation, only calendar-time coverage.

Usage:
    python3 covid_exposure.py --start 1998-01 --end 2030-12 --out ../output/covid_exposure.csv

Output: one row per (year, month), ready for a Stata `merge m:1 year month
using covid_exposure` (export --stata-dta writes a .dta directly if wanted).
"""
import argparse
import csv
from datetime import date
from pathlib import Path

PHEIC_START = (2020, 1)   # WHO PHEIC declared 2020-01-30
PHEIC_END = (2023, 5)     # WHO PHEIC ended 2023-05-05
ACUTE_START = (2020, 3)
ACUTE_END = (2021, 6)


def month_range(start: str, end: str):
    sy, sm = (int(x) for x in start.split("-"))
    ey, em = (int(x) for x in end.split("-"))
    y, m = sy, sm
    while (y, m) <= (ey, em):
        yield y, m
        m += 1
        if m == 13:
            m = 1
            y += 1


def in_window(y: int, m: int, start: tuple, end: tuple) -> int:
    return int(start <= (y, m) <= end)


def build_table(start: str, end: str):
    rows = []
    for y, m in month_range(start, end):
        pheic = in_window(y, m, PHEIC_START, PHEIC_END)
        acute = in_window(y, m, ACUTE_START, ACUTE_END)
        intensity = 2 if acute else (1 if pheic else 0)
        rows.append(
            {
                "year": y,
                "month": m,
                "yyyymm": f"{y:04d}-{m:02d}",
                "covid_pheic": pheic,
                "covid_acute": acute,
                "covid_intensity": intensity,
            }
        )
    return rows


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--start", default="1998-01", help="YYYY-MM, inclusive")
    ap.add_argument("--end", default=None, help="YYYY-MM, inclusive (default: December, current year + 4)")
    ap.add_argument("--out", default="../output/covid_exposure.csv")
    args = ap.parse_args()

    end = args.end or f"{date.today().year + 4}-12"
    rows = build_table(args.start, end)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["year", "month", "yyyymm", "covid_pheic", "covid_acute", "covid_intensity"])
        w.writeheader()
        w.writerows(rows)

    n_pheic = sum(r["covid_pheic"] for r in rows)
    n_acute = sum(r["covid_acute"] for r in rows)
    print(f"Wrote {len(rows)} months ({args.start} to {end}) to {out_path}")
    print(f"  covid_pheic=1 for {n_pheic} months (2020-01 to 2023-05)")
    print(f"  covid_acute=1 for {n_acute} months (2020-03 to 2021-06)")


if __name__ == "__main__":
    main()
