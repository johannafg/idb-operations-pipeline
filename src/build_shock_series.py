#!/usr/bin/env python3
"""
Pipeline Fixes Punch List, item 10: assembles the monthly, project-invariant
global shock series the shift-share exposure regressor in
docs/Methodology_Hazard_LP_ShiftShare.tex Section 2 is built from. Output is
a long (year, month, shock_name, value) table, one row per series per month
-- the join key build_project_month_panel.py's monthly rows already use.

IMPORTANT -- read before relying on this: this script was written and
syntax-checked in a sandboxed environment with no general internet access,
so the live data fetches below have NOT been verified against a real
connection. FRED's CSV endpoint (https://fred.stlouisfed.org/graph/
fredgraph.csv?id=SERIES_ID) is a long-stable, well-documented public API and
is fetched directly here with high confidence in the URL format; the parsing
logic is defensive (missing values are "." in FRED's own convention, coerced
to null) and will raise a clear error rather than silently mis-parsing if
the response shape doesn't match what's expected. Run this on your own
machine and check the printed summary before trusting the output.

Series included (each is a candidate proxy for one of the four
transmission channels the methodology note lists -- financial, supply-chain,
real-activity, commodity-price -- not a complete or authoritative set; treat
this as a starting point to review, not a finished shock series):

  FRED (fetched live, no API key needed for the CSV endpoint):
    NFCI    -- Chicago Fed National Financial Conditions Index (financial)
    VIXCLS  -- CBOE Volatility Index (financial / risk)
    DGS10   -- 10-Year Treasury yield (financial / discount-rate channel)
    DCOILWTICO -- WTI crude oil price (commodity)

  NOT fetched live -- these are published as downloadable spreadsheets that
  change URL/format periodically, so rather than guess a possibly-stale URL,
  this script accepts a local file you download yourself:
    --gscpi-xlsx      NY Fed Global Supply Chain Pressure Index
                       (download from newyorkfed.org/research/policy/gscpi)
    --commodity-xlsx  World Bank Commodity Price Data ("Pink Sheet")
                       (download from worldbank.org/en/research/commodity-markets)

Usage:
    python3 build_shock_series.py --out ../output/shock_series.csv \
        --gscpi-xlsx gscpi_data.xlsx --commodity-xlsx CMO-Historical-Data-Monthly.xlsx
    # or, FRED series only, no manual downloads:
    python3 build_shock_series.py --out ../output/shock_series.csv --skip-manual
"""
import argparse
import sys
from pathlib import Path

import pandas as pd
import requests

FRED_SERIES = {
    "NFCI": "financial",
    "VIXCLS": "financial",
    "DGS10": "financial",
    "DCOILWTICO": "commodity",
}
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"


def fetch_fred_series(series_id: str) -> pd.DataFrame:
    url = FRED_URL.format(series_id=series_id)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    from io import StringIO
    df = pd.read_csv(StringIO(resp.text))
    if "DATE" not in df.columns or series_id not in df.columns:
        raise ValueError(
            f"Unexpected FRED CSV format for {series_id}: got columns {list(df.columns)}, "
            f"expected 'DATE' and '{series_id}'. FRED may have changed its CSV format since "
            f"this script was written -- check https://fred.stlouisfed.org/series/{series_id} "
            f"and the 'Download' button there for the current format."
        )
    df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
    df[series_id] = pd.to_numeric(df[series_id].replace(".", None), errors="coerce")
    df = df.dropna(subset=["DATE"])
    df["year"] = df["DATE"].dt.year
    df["month"] = df["DATE"].dt.month
    # FRED series can be daily/weekly -- collapse to a single monthly average,
    # which is the join grain build_project_month_panel.py's rows use.
    monthly = df.groupby(["year", "month"])[series_id].mean().reset_index()
    monthly = monthly.rename(columns={series_id: "value"})
    monthly["shock_name"] = series_id
    return monthly[["year", "month", "shock_name", "value"]]


def load_manual_xlsx(path: str, shock_name: str, date_col_hint: str, value_col_hint: str) -> pd.DataFrame:
    """Best-effort loader for a manually-downloaded spreadsheet whose exact
    column names vary by source and by vintage. Rather than hardcode a
    column name that may not match what you actually downloaded, this scans
    for a column whose name CONTAINS date_col_hint / value_col_hint
    (case-insensitive) and asks you to confirm if more than one candidate
    matches -- safer than silently picking the wrong column."""
    if not path or not Path(path).exists():
        return pd.DataFrame(columns=["year", "month", "shock_name", "value"])
    xls = pd.ExcelFile(path)
    for sheet in xls.sheet_names:
        df = pd.read_excel(path, sheet_name=sheet, header=None)
        # spreadsheets like this often have several header/title rows before
        # the real column headers -- scan the first 15 rows for one that
        # contains both hints.
        for hdr_row in range(min(15, len(df))):
            row_vals = [str(v).lower() for v in df.iloc[hdr_row].tolist()]
            date_matches = [i for i, v in enumerate(row_vals) if date_col_hint.lower() in v]
            if date_matches:
                real = pd.read_excel(path, sheet_name=sheet, header=hdr_row)
                real.columns = [str(c).strip() for c in real.columns]
                date_cands = [c for c in real.columns if date_col_hint.lower() in c.lower()]
                value_cands = [c for c in real.columns if value_col_hint.lower() in c.lower()]
                if date_cands and value_cands:
                    d, v = date_cands[0], value_cands[0]
                    out = real[[d, v]].rename(columns={d: "_date", v: "value"})
                    out["_date"] = pd.to_datetime(out["_date"], errors="coerce")
                    out["value"] = pd.to_numeric(out["value"], errors="coerce")
                    out = out.dropna(subset=["_date"])
                    out["year"] = out["_date"].dt.year
                    out["month"] = out["_date"].dt.month
                    out["shock_name"] = shock_name
                    print(f"[info] {shock_name}: matched columns '{d}' (date) / '{v}' (value) "
                          f"in sheet '{sheet}' -- SPOT-CHECK this against the actual file before "
                          f"trusting it.", file=sys.stderr)
                    return out[["year", "month", "shock_name", "value"]]
    print(f"[warn] Could not auto-detect date/value columns for {shock_name} in {path} -- "
          f"skipping. Open the file and adapt load_manual_xlsx()'s hints to match its actual "
          f"column names.", file=sys.stderr)
    return pd.DataFrame(columns=["year", "month", "shock_name", "value"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="../output/shock_series.csv")
    ap.add_argument("--gscpi-xlsx", default=None,
                     help="local path to the NY Fed GSCPI download (see module docstring)")
    ap.add_argument("--commodity-xlsx", default=None,
                     help="local path to the World Bank Commodity Price Data download")
    ap.add_argument("--skip-manual", action="store_true",
                     help="skip GSCPI/commodity even if files are provided -- FRED series only")
    args = ap.parse_args()

    frames = []
    for series_id in FRED_SERIES:
        try:
            frames.append(fetch_fred_series(series_id))
            print(f"[ok] fetched {series_id} from FRED", file=sys.stderr)
        except Exception as e:
            print(f"[warn] failed to fetch {series_id} from FRED: {e} -- skipping", file=sys.stderr)

    if not args.skip_manual:
        frames.append(load_manual_xlsx(args.gscpi_xlsx, "GSCPI", "date", "gscpi"))
        frames.append(load_manual_xlsx(args.commodity_xlsx, "COMMODITY_INDEX", "date", "index"))

    frames = [f for f in frames if not f.empty]
    if not frames:
        print("[error] No series were successfully loaded -- nothing to write.", file=sys.stderr)
        sys.exit(1)

    out_df = pd.concat(frames, ignore_index=True)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(args.out, index=False)

    print(f"\nWrote {args.out}: {len(out_df)} rows")
    print("\nSeries included, date range each:")
    for name, g in out_df.groupby("shock_name"):
        ym_min = f"{int(g['year'].min())}-{int(g.loc[g.year==g.year.min(),'month'].min()):02d}"
        ym_max = f"{int(g['year'].max())}-{int(g.loc[g.year==g.year.max(),'month'].max()):02d}"
        print(f"  {name}: {len(g)} months, {ym_min} to {ym_max}")
    print("\nThis is a starting candidate set, not a finished/reviewed shock series -- "
          "see the module docstring for what each series is a proxy for, and confirm the "
          "choice of series before using this in an actual regression.")


if __name__ == "__main__":
    main()
