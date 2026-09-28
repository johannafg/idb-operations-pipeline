#!/usr/bin/env python3
"""
Pipeline Fixes Punch List, item 11 (Specification C only): builds
CountryShare_{ck,t-1} -- the share of country c's ACTIVE loan portfolio, as
of the prior period, exposed via transmission channel k -- from the
Methodology_Approval_Rate_Extensive_Margin.tex construction (Section "The
Structural Difference: No Pre-Existing Project Shares"). This is the
country-level analogue of the project-level shares already used in
Specifications A/B, aggregated up from the same underlying extracted fields
rather than a new extraction.

"Active as of period t" = approved on or before t AND not yet exited by t
(uses build_project_month_panel.py's per-project spell_start/exit info,
so an already-cancelled or already-fully-disbursed project correctly drops
out of a country's active portfolio once it exits, rather than being counted
for its entire nominal life).

Grain is ANNUAL by default (--freq year), not monthly or quarterly: an
active portfolio share computed from a handful of projects gets noisy fast
at finer frequency, especially for the smaller borrowing countries, and the
extensive-margin note's own robustness check (i) flags this exact frequency
choice as something to settle empirically -- annual is the conservative
starting point, --freq quarter is available to test sensitivity.

Channels (four, mirroring the project-level shares already extracted --
NOT a new extraction, just an aggregation choice; each is a heuristic,
documented individually, and should be sanity-checked before being treated
as a finished exposure share):

  import_share         -- dollar-weighted share of active portfolio (by
                           idb_musd) with imported_inputs_present == True.
  cofinancing_share     -- dollar-weighted average of cofinancing_share_pct
                           where stated, else treated as 0 for projects
                           where cofinancing_present == False, else null
                           (not assumed zero) where truly not stated.
  fx_foreign_share      -- dollar-weighted share of active portfolio whose
                           fx_denomination text does NOT indicate local-
                           currency disbursement. Keyword heuristic (see
                           FX_LOCAL_KEYWORDS below) -- spot-check against a
                           sample of the actual fx_denomination text before
                           trusting this channel specifically; free-text
                           fields are the least reliable to aggregate this
                           way of the four.
  commodity_sector_share -- dollar-weighted share of active portfolio in a
                           sector keyword-matched as commodity-price-exposed
                           (energy, agriculture, mining/extractives --
                           see COMMODITY_SECTOR_KEYWORDS). Also a heuristic.

Usage:
    python3 build_country_portfolio_shares.py \
        --panel ../output/microdata_panel_latest.xlsx \
        --panel-summary ../output/project_month_panel_summary.csv \
        --freq year \
        --out ../output/country_portfolio_shares.csv
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

from opnum import LOAN_PATTERN

FX_LOCAL_KEYWORDS = ("local currency", "national currency", "moneda local", "moeda local")
COMMODITY_SECTOR_KEYWORDS = ("energy", "agricult", "mining", "extractive", "oil", "gas", "mineral")


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


def period_of(year: int, month: int, freq: str):
    if freq == "quarter":
        return (year, (month - 1) // 3 + 1)
    return (year,)  # annual


def period_range(start, end, freq: str):
    """Inclusive list of periods from start to end, both as (year,) or
    (year, quarter) tuples matching period_of()'s output shape."""
    periods = []
    if freq == "quarter":
        y, q = start
        ey, eq = end
        while (y, q) <= (ey, eq):
            periods.append((y, q))
            q += 1
            if q == 5:
                q = 1
                y += 1
    else:
        y = start[0]
        while y <= end[0]:
            periods.append((y,))
            y += 1
    return periods


def is_fx_foreign(fx_text):
    if fx_text is None:
        return None
    t = str(fx_text).lower()
    if any(kw in t for kw in FX_LOCAL_KEYWORDS):
        return False
    return True  # any other stated currency text is treated as non-local/foreign-denominated


def is_commodity_sector(sector_text):
    if sector_text is None:
        return False
    t = str(sector_text).lower()
    return any(kw in t for kw in COMMODITY_SECTOR_KEYWORDS)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--panel", default="../output/microdata_panel_latest.xlsx")
    ap.add_argument("--panel-summary", default="../output/project_month_panel_summary.csv")
    ap.add_argument("--freq", choices=["year", "quarter"], default="year")
    ap.add_argument("--out", default="../output/country_portfolio_shares.csv")
    args = ap.parse_args()

    panel = load_panel(args.panel)
    if not Path(args.panel_summary).exists():
        print(f"[error] {args.panel_summary} not found -- run build_project_month_panel.py first, "
              f"this script needs each project's resolved spell window to know when it exits "
              f"a country's active portfolio.", file=sys.stderr)
        sys.exit(1)
    summ = pd.read_csv(args.panel_summary)
    summ = summ[summ["status"] == "ok"].copy()

    panel["approval_date"] = pd.to_datetime(panel["approval_date"], errors="coerce")
    panel = panel.merge(summ[["operation_number", "spell_start", "n_months"]], on="operation_number", how="left")
    panel = panel.dropna(subset=["approval_date", "country_iso3"]).copy()

    # exit period = spell_start + n_months (months); still-active/unresolved
    # projects (n_months null) are treated as active through the end of the
    # observed horizon -- they simply never drop out of the active portfolio
    # in this construction, which is the conservative choice (a project we
    # can't confirm exited stays counted as exposed).
    def compute_exit_ym(row):
        if pd.isna(row.get("n_months")) or pd.isna(row.get("spell_start")):
            return None
        y, m = (int(x) for x in str(row["spell_start"]).split("-"))
        total = y * 12 + (m - 1) + int(row["n_months"])
        return total // 12, total % 12 + 1

    panel["exit_ym"] = panel.apply(compute_exit_ym, axis=1)
    panel["approval_ym"] = list(zip(panel["approval_date"].dt.year, panel["approval_date"].dt.month))

    idb_musd = pd.to_numeric(panel.get("idb_musd"), errors="coerce")
    panel["_weight"] = idb_musd.fillna(0)
    # build_excel_report.py's build_panel_sheet() renders booleans as the
    # strings "Yes"/"No" in the xlsx (not "True"/"False") -- match both
    # spellings so this works whether the panel was loaded from the xlsx
    # (Yes/No) or the raw csv (True/False, from parse_cell()).
    # imported_inputs_present was removed from schema.py on 2026-09-23: across
    # every run it was correct for roughly one in four operations it flagged,
    # picking up regional trade, economic-analysis assumptions and disease
    # cases rather than the project's own procured inputs. Panels built after
    # that date have no such column, so the import_share channel is emitted as
    # null rather than computed from an unreliable field. Older panels still
    # carrying the column are still processed, so this script reproduces
    # earlier results unchanged.
    _import_col = panel.get("imported_inputs_present")
    if _import_col is None:
        print("[note] panel has no imported_inputs_present column -- import_share "
              "will be null for every row (field removed from the schema).", file=sys.stderr)
        panel["_import_flag"] = None
    else:
        panel["_import_flag"] = _import_col.map(
            lambda v: True if v is True or str(v).strip().lower() in ("true", "yes") else
                      (False if v is False or str(v).strip().lower() in ("false", "no") else None))
    panel["_cofin_pct"] = pd.to_numeric(panel.get("cofinancing_share_pct"), errors="coerce")
    cofin_present_str = panel.get("cofinancing_present").astype(str).str.strip().str.lower()
    panel.loc[panel["_cofin_pct"].isna() & cofin_present_str.isin(["false", "no"]), "_cofin_pct"] = 0.0
    panel["_fx_foreign"] = panel.get("fx_denomination").map(is_fx_foreign)
    panel["_commodity_sector"] = panel.get("sector").map(is_commodity_sector)

    if panel["approval_ym"].isna().all():
        print("[error] No projects with a usable approval_ym -- nothing to compute.", file=sys.stderr)
        sys.exit(1)
    start_period = period_of(*min(panel["approval_ym"]), args.freq)
    all_ym = pd.to_datetime(panel["approval_date"]).dropna()
    end_period = period_of(pd.Timestamp.today().year, pd.Timestamp.today().month, args.freq)
    periods = period_range(start_period, end_period, args.freq)

    rows = []
    for period in periods:
        period_end_ym = (period[0], 12) if args.freq == "year" else (period[0], period[1] * 3)
        active = panel[
            panel["approval_ym"].map(lambda ym: ym is not None and ym <= period_end_ym) &
            (panel["exit_ym"].isna() | panel["exit_ym"].map(lambda ym: ym is None or ym > period_end_ym))
        ]
        for country, g in active.groupby("country_iso3"):
            w = g["_weight"]
            total_w = w.sum()

            def wavg(series):
                s = series.dropna()
                if not len(s) or total_w == 0:
                    return None
                ws = w.loc[s.index]
                if ws.sum() == 0:
                    return round(s.mean(), 4)
                return round((s.astype(float) * ws).sum() / ws.sum(), 4)

            rows.append({
                "country_iso3": country, "period": "-".join(str(x) for x in period),
                "n_active_projects": len(g),
                "import_share": wavg(g["_import_flag"].astype("float")),
                "cofinancing_share": wavg(g["_cofin_pct"] / 100.0),
                "fx_foreign_share": wavg(g["_fx_foreign"].astype("float")),
                "commodity_sector_share": wavg(g["_commodity_sector"].astype("float")),
            })

    out_df = pd.DataFrame(rows)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(args.out, index=False)
    print(f"Wrote {args.out}: {len(out_df)} country-period rows "
          f"({out_df['country_iso3'].nunique()} countries x {len(periods)} periods, freq={args.freq})")
    print("\nCoverage:")
    for col in ["import_share", "cofinancing_share", "fx_foreign_share", "commodity_sector_share"]:
        print(f"  {col}: {out_df[col].notna().sum()}/{len(out_df)} populated")
    print("\nThese are heuristic aggregations of already-extracted project-level fields, not a "
          "new extraction -- see the module docstring for each channel's exact construction and "
          "caveats before using this as an exposure regressor.")


if __name__ == "__main__":
    main()
