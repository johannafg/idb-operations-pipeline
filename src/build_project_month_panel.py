#!/usr/bin/env python3
"""
Stage 4: expand the cross-sectional microdata panel (one row per project)
into a project-month panel (one row per project per calendar month) -- the
structural gap flagged as Pipeline Fixes Punch List, item 2. Neither
Specification A (hazard) nor B (local projections) can be estimated on the
cross-sectional output of build_microdata_panel.py / build_excel_report.py
directly; this is the missing step between them and an actual regression.

WHAT THIS DOES, per project:

  1. Spell start = eligibility_date_stated if populated, else approval_date (falls
     back silently, per docs/Methodology_Hazard_LP_ShiftShare.tex Section 1
     -- eligibility_date_stated is a schema.py field added 2026-09-21 and isn't
     backfilled in the panel yet as of this writing, so every project is
     currently on the fallback; re-run this script once it is).

  2. outcome_resolution: "transaction_level" if the operation has ANY
     DISBURSEMENT row in the IATI transactions export, else "snapshot_only".
     This distinction matters because the transactions export has a hard
     coverage cutoff at 2018-02 (see Pipeline Fixes Punch List, item 9) --
     it is NOT updated past that point. A "transaction_level" project gets
     real monthly disbursed amounts and a cumulative-share trajectory;
     a "snapshot_only" project gets risk-months with no disbursement detail
     in between, resolved only at its terminal month via
     totally_disbursed_date (if known).

  3. Event definition: y_it = 1 in the first month cumulative disbursed
     amount / idb_musd crosses --threshold (default 0.98), i.e. the project
     is treated as "done" once at least 98% of IDB's own committed amount
     has been disbursed -- deliberately not 100%, since small residual
     balances routinely go undisbursed at closeout. idb_musd (not
     total_musd) is the denominator because it's IDB's own committed
     capital net of cofinancing/counterpart, which is what a DISBURSEMENT
     transaction record actually draws down.

  4. Exit reason per project: "disbursed_complete" (event above),
     "cancelled" (terminal_state == cancelled), "right_censored" (still
     active, or transaction_level exhausted its data before resolving),
     or "unresolved_at_cutoff".

KNOWN LIMITATION, not fixed here: there is no cancellation DATE anywhere in
the pipeline (see Pipeline Fixes Punch List -- this is a new gap surfaced by
writing this script, not on the original list). A cancelled project is
right-censored at the data cutoff with exit_reason="cancelled_no_date" rather
than exited at its true cancellation month, which understates its
contribution to the competing-risks hazard. Worth fixing by extracting
data.xlsx's "Last Updated" column as a cancellation-date proxy in a follow-up
to build_excel_report.py's load_bulk_metadata().

Usage:
    python3 build_project_month_panel.py \
        --panel ../output/microdata_panel_latest.xlsx \
        --transactions ../corpus/idb-iati-dataset-transactions.csv \
        --covid-exposure covid_exposure.csv \
        --out ../output/project_month_panel.csv
"""
import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from opnum import LOAN_PATTERN

TRANSACTIONS_MAX_YM = (2018, 2)  # hard coverage cutoff -- see module docstring


def ym(d: date) -> tuple:
    return (d.year, d.month)


def add_months(y: int, m: int, n: int) -> tuple:
    total = (y * 12 + (m - 1)) + n
    return total // 12, total % 12 + 1


def month_diff(a: tuple, b: tuple) -> int:
    """Months from a to b, b - a, in month units."""
    return (b[0] * 12 + b[1]) - (a[0] * 12 + a[1])


def load_cross_sectional_panel(path: str) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        df = pd.read_excel(p, sheet_name="Microdata Panel")
    else:
        df = pd.read_csv(p, dtype=str, keep_default_na=False)
    # Both build_excel_report.py's "not stated"/"no source match" placeholders
    # and pandas' own NaN need to collapse to a single missing-value
    # representation before any date parsing or numeric coercion below.
    df = df.replace({"not stated": None, "no source match": None, "": None})
    # The Microdata Panel sheet appends a blank spacer row and a legend/notes
    # row below the real data (see Pipeline Fixes Punch List, item 6) --
    # both share the same column layout, so filter to real operation numbers
    # only rather than let them show up as bogus "skipped" projects below.
    op_re = LOAN_PATTERN   # both numbering schemes -- see opnum.py
    df = df[df["operation_number"].astype(str).str.match(op_re, na=False)]
    return df


def load_transactions(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    df = df[df["Transaction type"] == "DISBURSEMENT"].copy()
    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)
    df["Year"] = pd.to_numeric(df["Year"], errors="coerce").astype("Int64")
    df["Month"] = pd.to_numeric(df["Month"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["Year", "Month", "Operation number"])
    monthly = (df.groupby(["Operation number", "Year", "Month"])["Amount"]
                 .sum().reset_index()
                 .rename(columns={"Operation number": "operation_number",
                                   "Year": "year", "Month": "month",
                                   "Amount": "monthly_disbursed_usd"}))
    return monthly


JOIN_MISMATCH_YEARS = 2  # see load_earliest_transaction_dates() docstring


def load_earliest_transaction_dates(path: str) -> dict:
    """Returns {operation_number: (year, month)} of the EARLIEST transaction
    of any type (not just DISBURSEMENT) for that operation number in the
    IATI transactions export. Used as a join-integrity check: if this
    predates a project's own spell_start (approval/eligibility date) by more
    than JOIN_MISMATCH_YEARS, the two "operation_number" values almost
    certainly refer to different underlying loans that happen to collide on
    the same string -- confirmed on the real corpus (2026-09-23): checked
    all 1,801 transaction_level projects, found 8 (BR-L1363, EC-L1111,
    HO-L1013, HO-L1020, JA0114, PE-L1024, PN-L1005, PN-L1012) where the earliest
    COMMITMENT/DISBURSEMENT record (rechecked 2026-09-27 against the registry
    transactions file, which runs to 2026-08; the earlier note said 4 of 1,032
    against the frozen 2004-2018 export)
    PN-L1012, PN-L1053) where the earliest COMMITMENT/DISBURSEMENT record
    predates approval_date by 3-8 years, which cannot reflect the same loan.
    A degenerate case (EC-L1111, approved 2018-04, one month after the
    transactions coverage cutoff) is what originally surfaced this: it
    produced a 0-month spell with a nonsensical exit_reason instead of being
    excluded outright. See Pipeline Fixes Punch List for the fuller writeup."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    df["Year"] = pd.to_numeric(df["Year"], errors="coerce").astype("Int64")
    df["Month"] = pd.to_numeric(df["Month"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["Year", "Month", "Operation number"])
    earliest = {}
    for op, g in df.groupby("Operation number"):
        ym_list = list(zip(g["Year"].astype(int), g["Month"].astype(int)))
        earliest[op] = min(ym_list)
    return earliest


def load_covid_exposure(path: str) -> pd.DataFrame:
    if not path or not Path(path).exists():
        print(f"[warn] --covid-exposure file not found at '{path}' -- covid_pheic/covid_acute/"
              f"covid_intensity will be entirely null in the output, not an error, but silently "
              f"missing a real covariate. Run covid_exposure.py first if this wasn't intentional.",
              file=sys.stderr)
        return pd.DataFrame(columns=["year", "month", "covid_pheic", "covid_acute", "covid_intensity"])
    df = pd.read_csv(path)
    return df[["year", "month", "covid_pheic", "covid_acute", "covid_intensity"]]


def parse_date_cell(v):
    if v is None:
        return None
    dt = pd.to_datetime(v, errors="coerce")
    if pd.isna(dt):
        return None
    return (dt.year, dt.month)


def build_project_spell(row: pd.Series, tx_by_op: dict, data_cutoff_ym: tuple, threshold: float,
                         earliest_tx_ym: dict = None):
    """Returns (list of month rows, summary dict) for one project. Isolated
    from the main loop so a single project's edge cases can be unit-tested
    without needing the full panel loaded."""
    op = row["operation_number"]
    eligibility = parse_date_cell(row.get("eligibility_date_stated"))
    approval = parse_date_cell(row.get("approval_date"))
    spell_start = eligibility or approval
    if spell_start is None:
        return [], {"operation_number": op, "status": "skipped_no_start_date"}

    idb_musd = pd.to_numeric(row.get("idb_musd"), errors="coerce")
    idb_usd = float(idb_musd) * 1_000_000 if pd.notna(idb_musd) and idb_musd > 0 else None

    terminal_state = row.get("terminal_state")
    totally_disbursed = parse_date_cell(row.get("totally_disbursed_date"))

    # Join-integrity check (see load_earliest_transaction_dates()): if this
    # operation_number's earliest transactions.csv record predates its own
    # spell_start by more than JOIN_MISMATCH_YEARS, the transactions data
    # almost certainly belongs to a different loan that collides on the same
    # operation_number string -- don't build a monthly disbursement path
    # from it. Falls through to the same snapshot_only handling as a project
    # with no transactions data at all, just with a flag recording why.
    join_mismatch = False
    if earliest_tx_ym and op in earliest_tx_ym:
        gap_months = month_diff(earliest_tx_ym[op], spell_start)
        if abs(gap_months) > JOIN_MISMATCH_YEARS * 12:
            join_mismatch = True

    # No true cancellation date exists anywhere upstream (Pipeline Fixes
    # Punch List, item 13) -- data.xlsx's Last Updated was tried as a proxy,
    # but empirically it's unreliable for most cancelled projects: 68 of 77
    # land in the SAME MONTH as spell_start, which isn't a credible
    # cancellation timeline for a real IDB loan (cancellations virtually
    # always follow a year or more of non-performance) -- almost certainly
    # reflects a database record that was never touched again after
    # creation, not a true last-modified timestamp. Trusting it as-is would
    # inject a strong downward bias into cancelled projects' survival time.
    # MIN_PLAUSIBLE_CANCEL_MONTHS floors this: only 9 of 77 clear 12 months
    # and get a real date-bounded cancellation; the other 68 correctly fall
    # back to cancelled_no_date rather than a fabricated early exit.
    MIN_PLAUSIBLE_CANCEL_MONTHS = 12
    cancel_ym = None
    if terminal_state == "cancelled":
        raw_cancel_ym = parse_date_cell(row.get("last_updated"))
        if raw_cancel_ym and month_diff(spell_start, raw_cancel_ym) >= MIN_PLAUSIBLE_CANCEL_MONTHS:
            cancel_ym = raw_cancel_ym

    op_tx = None if join_mismatch else tx_by_op.get(op)  # dict {(y,m): amount}, or None
    outcome_resolution = "transaction_level" if op_tx else "snapshot_only"

    rows = []
    cum = 0.0
    event_ym = None
    exit_reason = None

    if outcome_resolution == "transaction_level" and idb_usd:
        # Walk month by month from spell_start to the earlier of the
        # transactions coverage cutoff, today, or (for a cancelled project
        # with a usable proxy date) the cancellation month -- stop early
        # the month the cumulative share crosses --threshold.
        cursor = spell_start
        end_bound = min(TRANSACTIONS_MAX_YM, data_cutoff_ym)
        if cancel_ym:
            end_bound = min(end_bound, cancel_ym)
        spell_month = 0
        while cursor <= end_bound:
            monthly_amt = op_tx.get(cursor, 0.0)
            cum += monthly_amt
            share = cum / idb_usd
            crossed = share >= threshold
            rows.append({
                "operation_number": op, "year": cursor[0], "month": cursor[1],
                "spell_month": spell_month, "monthly_disbursed_usd": monthly_amt,
                "cum_disbursed_usd": cum, "cum_disbursement_share": round(share, 4),
                "y_it": int(crossed), "at_risk": 1, "outcome_resolution": outcome_resolution,
            })
            if crossed:
                event_ym = cursor
                exit_reason = "disbursed_complete"
                break
            cursor = add_months(*cursor, 1)
            spell_month += 1
        else:
            # Ran out of transaction coverage (or hit today) without
            # crossing threshold. If the bulk source separately says this
            # project IS fully disbursed, trust totally_disbursed_date for
            # the terminal label even though we can't show the monthly
            # path past the transactions cutoff -- flag it rather than
            # silently extending fabricated monthly rows.
            if terminal_state == "disbursed_complete" and totally_disbursed:
                exit_reason = "disbursed_complete_post_transactions_cutoff"
            elif terminal_state == "cancelled":
                exit_reason = "cancelled" if cancel_ym else "cancelled_no_date"
            else:
                exit_reason = "right_censored_at_transactions_cutoff"

    else:
        # snapshot_only: no monthly disbursement detail at all. Emit risk
        # months with null disbursement fields up to the terminal month
        # (totally_disbursed_date if known and terminal_state agrees;
        # otherwise the data cutoff for still-active projects, or an
        # unresolved flag for cancelled projects with no date on record).
        if terminal_state == "disbursed_complete" and totally_disbursed:
            end_bound = totally_disbursed
            exit_reason = "disbursed_complete"
            event_ym = totally_disbursed
        elif terminal_state == "still_active":
            end_bound = data_cutoff_ym
            exit_reason = "right_censored"
        elif terminal_state == "cancelled" and cancel_ym:
            end_bound = cancel_ym
            exit_reason = "cancelled"
        else:  # cancelled with no usable date, or disbursed_complete with no totally_disbursed_date
            end_bound = data_cutoff_ym
            exit_reason = "cancelled_no_date" if terminal_state == "cancelled" else "unresolved_at_cutoff"

        if end_bound < spell_start:
            return [], {"operation_number": op, "status": "skipped_end_before_start"}

        cursor = spell_start
        spell_month = 0
        while cursor <= end_bound:
            is_event_month = (cursor == event_ym)
            rows.append({
                "operation_number": op, "year": cursor[0], "month": cursor[1],
                "spell_month": spell_month, "monthly_disbursed_usd": None,
                "cum_disbursed_usd": None, "cum_disbursement_share": None,
                "y_it": int(is_event_month), "at_risk": 1, "outcome_resolution": outcome_resolution,
            })
            cursor = add_months(*cursor, 1)
            spell_month += 1

    summary = {
        "operation_number": op, "status": "ok", "n_months": len(rows),
        "join_mismatch_suspected": join_mismatch,
        "outcome_resolution": outcome_resolution, "exit_reason": exit_reason,
        "spell_start": f"{spell_start[0]:04d}-{spell_start[1]:02d}",
    }
    return rows, summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--panel", default="../output/microdata_panel_latest.xlsx")
    ap.add_argument("--transactions", default="../corpus/idb-iati-dataset-transactions.csv")
    ap.add_argument("--covid-exposure", default="../output/covid_exposure.csv")
    ap.add_argument("--out", default="../output/project_month_panel.csv")
    ap.add_argument("--summary-out", default="../output/project_month_panel_summary.csv")
    ap.add_argument("--threshold", type=float, default=0.98,
                     help="cumulative disbursement share treated as 'complete' (default 0.98)")
    ap.add_argument("--data-cutoff", default=None,
                     help="YYYY-MM right-censoring point for still-active/snapshot-only projects "
                          "(default: current month)")
    args = ap.parse_args()

    cutoff = tuple(int(x) for x in args.data_cutoff.split("-")) if args.data_cutoff else (date.today().year, date.today().month)

    panel = load_cross_sectional_panel(args.panel)
    tx_monthly = load_transactions(args.transactions)
    tx_by_op = {}
    for op, g in tx_monthly.groupby("operation_number"):
        tx_by_op[op] = {(int(r.year), int(r.month)): float(r.monthly_disbursed_usd) for r in g.itertuples()}
    earliest_tx_ym = load_earliest_transaction_dates(args.transactions)
    covid = load_covid_exposure(args.covid_exposure)

    all_rows, summaries = [], []
    for _, row in panel.iterrows():
        rows, summ = build_project_spell(row, tx_by_op, cutoff, args.threshold, earliest_tx_ym)
        all_rows.extend(rows)
        summaries.append(summ)

    out_df = pd.DataFrame(all_rows)
    if not out_df.empty:
        out_df = out_df.merge(covid, on=["year", "month"], how="left")
        # carry a few project-level fields through for convenience so this
        # file is usable without a second merge back to the cross-sectional
        # panel for the most common covariates.
        carry_cols = [c for c in ("country_iso3", "is_regional", "lending_instrument",
                                    "terminal_state", "idb_musd") if c in panel.columns]
        carry = panel[["operation_number"] + carry_cols].drop_duplicates("operation_number")
        out_df = out_df.merge(carry, on="operation_number", how="left")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(args.out, index=False)

    summ_df = pd.DataFrame(summaries)
    summ_df.to_csv(args.summary_out, index=False)

    n_ok = (summ_df["status"] == "ok").sum() if not summ_df.empty else 0
    n_skipped = len(summ_df) - n_ok
    print(f"Wrote {args.out}: {len(out_df)} project-month rows across {n_ok} project(s)")
    print(f"Wrote {args.summary_out}: per-project resolution summary "
          f"({n_skipped} project(s) skipped -- see 'status' column)")
    if not summ_df.empty and n_ok:
        print("\nexit_reason distribution:")
        print(summ_df[summ_df["status"] == "ok"]["exit_reason"].value_counts().to_string())
        print("\noutcome_resolution distribution:")
        print(summ_df[summ_df["status"] == "ok"]["outcome_resolution"].value_counts().to_string())
        n_mismatch = summ_df[summ_df["status"] == "ok"]["join_mismatch_suspected"].sum()
        if n_mismatch:
            mismatched_ops = summ_df[summ_df["join_mismatch_suspected"] == True]["operation_number"].tolist()
            print(f"\n[warn] {n_mismatch} operation(s) had a suspected operation_number collision "
                  f"with the transactions file (earliest transaction record more than "
                  f"{JOIN_MISMATCH_YEARS} years before/after the project's own spell_start) -- "
                  f"treated as snapshot_only rather than trusted as transaction_level: "
                  f"{mismatched_ops}")


if __name__ == "__main__":
    main()
