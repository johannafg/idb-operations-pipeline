"""
Turns the real output of build_microdata_panel.py (microdata_panel.csv +
microdata_audit.json) into the same polished, four-sheet workbook built by
hand for the CO-L1234 / AR-L1436 two-project test -- but driven entirely by
whatever is actually in those two files, so it scales to however many
projects a real harvest-and-extract run produced.

Sheets:
  Read Me            -- what this file is, how it was produced, a dry-run
                         warning if every value came back null (usually
                         means ANTHROPIC_API_KEY wasn't set for that run)
  Microdata Panel    -- one row per project, one column per schema field
  Citations & Audit  -- one row per project-field, with citation + quote
  Field Definitions  -- glossary, pulled directly from schema.py so it can
                         never drift out of sync with the actual schema

Usage:
    python3 build_excel_report.py --panel ../output/microdata_panel_latest.csv \
        --audit ../output/microdata_audit.json \
        --out   ../output/microdata_panel_latest.xlsx
    # optionally enrich with country / lending instrument / approval date
    # (only fills gaps --data-xlsx didn't already cover):
        --harvest-log harvest_log.csv
    # optionally enrich with project metadata (country, project name,
    # sector, modality, financing amounts, cofinancing agency/amount,
    # borrower, executing agency, env. category, sovereign guarantee,
    # bulk_status, approval date, and lending instrument for verified
    # modality codes) from the bulk "Download Project Information" export:
        --data-xlsx data.xlsx

Two different status taxonomies exist and don't map cleanly onto each other:
data.xlsx's bulk Status field only distinguishes EXITED/ACTIVE (kept here as
bulk_status, deliberately NOT named project_status, so it can't silently
shadow the richer field below when both --data-xlsx and --harvest-log are
passed together -- an earlier version of this script used the same column
name for both, which meant harvest_log's project_status was always dropped
whenever --data-xlsx was also given, since load_panel()'s extra_cols check
treats a name already in enriched_cols as already covered). harvest_log.csv's
project_status (scraped per-project) uses Closed/Cancelled/Implementation --
Cancelled is a real, meaningful category that bulk_status cannot express.
terminal_state (see derive_terminal_state, and load_panel() below) combines
the two, preferring harvest_log's project_status whenever it's populated and
falling back to bulk_status only for the active/exited distinction; an EXITED
project with no harvest_log corroboration is left "unknown" for manual
review rather than guessed. See the Pipeline Fixes Punch List, item 3.

Cofinancing agency/amount ARE derivable from the bulk export, but only
partially: data.xlsx has one row per funding source per project, and for
179 of the 2,581 projects in this corpus a second row names a real external
co-financier (e.g. China Co-Financing Fund, Korea Infrastructure
Development Co-Financing, Clean Technology Fund) with its own approved
amount. idb_musd/cofinancing_musd/counterpart_musd are computed by summing
those rows correctly per project (see IDB_OWN_CAPITAL_SOURCE_PREFIXES
below) -- an earlier version of this script took only the first row per
project, which silently understated idb_musd and misattributed the
cofinanced portion to counterpart_musd for those 47 projects. For the
other ~1170 projects, no cofinancing shows up in this export at all --
that could mean there genuinely isn't any, or that it's parallel financing
arranged outside IDB's own trust-fund accounting, which this file has no
visibility into either way.

Disbursement period in years is NOT in the bulk export (Tenor/Guarantee
Length is a different concept) -- it's added as its own schema.py field,
disbursement_period_years, extracted from each LP's disbursement schedule
the same way as the other 12 LLM-extracted fields (added 2026-08-13; see
schema.py and the Citations & Audit sheet for its citation/quote).

lending_instrument is derived from Modality Code, but only for the 9 codes
verified against real scraped harvest_log.csv rows (see
MODALITY_TO_VERIFIED_INSTRUMENT below); 22 projects using two unverified
codes (PDL, CND) are deliberately left "not stated" rather than guessed.

borrower is the legal/government entity responsible for repaying the
loan (from data.xlsx's Borrower column) -- a different role than
executing_agency, which implements the project day-to-day. The two are
often, but not always, the same organization.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

sys.path.insert(0, str(Path(__file__).parent))
from schema import FIELD_DEFS

FONT = "Arial"
NAVY = "1F3864"
WHITE = "FFFFFF"
GREY = "F2F2F2"
TRUE_FILL = PatternFill("solid", fgColor="E2EFDA")
FALSE_FILL = PatternFill("solid", fgColor="FCE4E4")
NULL_FILL = PatternFill("solid", fgColor=GREY)
STATUS_COLORS = {"found": "E2EFDA", "stated_absent": "FCE4E4", "not_stated": GREY}
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

FIELD_TYPES = {name: typ for name, typ, _ in FIELD_DEFS}

# Reasonable column widths for known fields; anything else (enriched columns
# like country, or a field added to the schema later) gets a generic width.
COLUMN_WIDTHS = {
    "operation_number": 14, "country": 16, "country_iso3": 14, "is_regional": 12,
    "lending_instrument": 20,
    "project_status": 14, "bulk_status": 14, "terminal_state": 20, "approval_date": 14,
    "eligibility_date": 16, "first_disbursement_date": 20, "totally_disbursed_date": 20,
    "current_disbursement_expiration_date": 24, "iati_instrument_type": 18,
    "last_updated": 16,
    "project_name": 44, "sector": 34, "modality": 16, "total_musd": 14,
    "idb_musd": 14, "cofinancing_musd": 16, "cofinancing_agency": 34,
    "counterpart_musd": 16, "borrower": 34, "executing_agency": 34,
    "env_category": 14, "sovereign_guarantee": 16,
    "cofinancing_present": 16, "cofinancing_share_pct": 16,
    "imported_inputs_present": 18, "imported_share_est": 16,
    "civil_works_type": 34, "construction_share_pct": 18,
    "procurement_modality": 30, "counterpart_funding_share_pct": 22,
    "fx_denomination": 30, "price_escalation_clause": 18,
    "executing_agency_type": 34, "safeguards_category": 14,
    "disbursement_period_years": 18,
}


def parse_cell(raw, field_name: str):
    """CSV round-trips everything through strings; coerce back to the type
    schema.py declares for this field so booleans/numbers render (and sort)
    correctly instead of as literal text."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip()
    if s == "" or s.lower() in ("none", "nan", "null"):
        return None
    field_type = FIELD_TYPES.get(field_name, "")
    if "boolean" in field_type:
        if s.lower() in ("true", "1", "yes"):
            return True
        if s.lower() in ("false", "0", "no"):
            return False
        return s  # unexpected value -- keep as-is rather than silently dropping it
    if "number" in field_type:
        try:
            return float(s)
        except ValueError:
            return s
    return s


# Best-effort decode for common IDB modality codes -- covers what's been
# seen in this corpus (see Modality Code value_counts in data.xlsx); NOT a
# complete official mapping. Codes not listed here are left as-is rather
# than guessed, since an inaccurate guess would be worse than a raw code.
KNOWN_MODALITY_CODES = {
    "ESP": "Specific Investment Operation",
    "PBL": "Policy-Based Loan",
    "PBP": "Programmatic Policy-Based Loan",
    "GOM": "Multiple Works Program",
    "SEF": "Sector Facility Loan",
    "LBR": "Results-Based Loan",
}

# lending_instrument isn't a column in the bulk export at all -- this maps
# Modality Code to it instead, but ONLY for codes verified against real
# harvest_log.csv rows that had a scraped instrument label (373 projects,
# cross-tabbed 2026-08-13: every one of these 9 codes mapped to "Investment
# Loan" with zero exceptions). Two codes present in this corpus (PDL, CND --
# 22 projects) never appeared in that verified sample, so they're
# deliberately left out here rather than guessed; those rows stay "not
# stated" for lending_instrument.
MODALITY_TO_VERIFIED_INSTRUMENT = {
    "ESP": "Investment Loan", "GOM": "Investment Loan", "GCR": "Investment Loan",
    "PPE": "Investment Loan", "SEF": "Investment Loan", "TCR": "Investment Loan",
    "IRF": "Investment Loan", "LBR": "Investment Loan", "INO": "Investment Loan",
}

# data.xlsx has ONE ROW PER FUNDING SOURCE, not one row per project -- 179 of
# the 2,581 projects in this corpus have 2+ rows (recounted 2026-09-27; the
# earlier figure of 47 of 1,217 predates the old-numbering harvest: e.g.
# BO-L1191 has an ORC-Ordinary Capital row for $50M AND a KIF-Korea
# Infrastructure Development Co-Financing row for $25M, both against the
# same $75M Total Cost). Naively taking the first row per operation_number
# (as an earlier version of this function did) silently understates
# idb_musd and misattributes the cofinanced portion to counterpart_musd.
# These prefixes are IDB's own balance-sheet capital windows, not external
# cofinancing -- everything else is a genuine external fund/agency co-
# financing the operation alongside IDB.
IDB_OWN_CAPITAL_SOURCE_PREFIXES = {"ORC", "FSO", "BLD"}

# data.xlsx's Project Country is free text, not a code -- and for regional /
# multi-country operations it's a single cell listing every IDB member
# country involved, semicolon-joined (confirmed 2026-09-21: rows listing all
# 26 borrowing members at once). This crosswalk covers every single-country
# value actually observed in this export (checked exhaustively against the
# full 28,656-row sheet, not just this corpus's 2,581 projects); "Regional"
# and "Not Defined" are real values in the export but aren't countries, so
# they're deliberately left unmapped (country_iso3 stays null) rather than
# assigned a code. See load_bulk_metadata() below for how is_regional and
# country_iso3 are derived from this. Pipeline Fixes Punch List, item 5.
ISO3_BY_COUNTRY = {
    "Argentina": "ARG", "Bahamas": "BHS", "Barbados": "BRB", "Belize": "BLZ",
    "Bolivia": "BOL", "Brazil": "BRA", "Chile": "CHL", "Colombia": "COL",
    "Costa Rica": "CRI", "Dominican Republic": "DOM", "Ecuador": "ECU",
    "El Salvador": "SLV", "Guatemala": "GTM", "Guyana": "GUY", "Haiti": "HTI",
    "Honduras": "HND", "Jamaica": "JAM", "Mexico": "MEX", "Nicaragua": "NIC",
    "Panama": "PAN", "Paraguay": "PRY", "Peru": "PER", "Suriname": "SUR",
    "Trinidad and Tobago": "TTO", "Uruguay": "URY", "Venezuela": "VEN",
}


def load_bulk_metadata(data_xlsx: str) -> pd.DataFrame:
    """Pulls the fields that are genuinely free -- already sitting in the
    bulk 'Download Project Information' export, no LP text extraction
    needed -- and derives a few more (counterpart amount, cofinancing
    agency/amount, sovereign guarantee flag) via arithmetic/lookup rather
    than an API call. total_musd/counterpart_musd validated against two
    real projects' LP-stated financing tables (AR-L1410, JA-L1093): Total
    Cost - (IDB + cofinancing) matched the LP's stated counterpart amount
    exactly in both cases."""
    raw = pd.read_excel(data_xlsx)
    raw.columns = [c.strip() for c in raw.columns]
    if "Project Number" not in raw.columns:
        return pd.DataFrame(columns=["operation_number"])
    raw = raw.dropna(subset=["Project Number"]).copy()

    def _get(col, frame=raw):
        return frame[col] if col in frame.columns else pd.Series([None] * len(frame), index=frame.index)

    fs = _get("Funding Source").fillna("")
    fs_code = fs.astype(str).str.split("-").str[0].str.strip()
    approval = pd.to_numeric(_get("Approval Amount"), errors="coerce").fillna(0)
    raw["_fs_code"] = fs_code
    raw["_is_idb_capital"] = fs_code.isin(IDB_OWN_CAPITAL_SOURCE_PREFIXES)
    raw["_approval"] = approval

    idb_amt = raw[raw["_is_idb_capital"]].groupby("Project Number")["_approval"].sum()
    cofin_amt = raw[~raw["_is_idb_capital"]].groupby("Project Number")["_approval"].sum()

    def _agency_names(sub):
        names = sub.loc[~sub["_is_idb_capital"], "Funding Source"].dropna().unique()
        return "; ".join(sorted(str(n).strip() for n in names)) if len(names) else None

    cofin_agency = raw.groupby("Project Number", group_keys=False).apply(_agency_names, include_groups=False)

    first = raw.drop_duplicates("Project Number", keep="first").set_index("Project Number")

    out = pd.DataFrame(index=first.index)
    out["operation_number"] = out.index
    out["country"] = _get("Project Country", first)
    # A semicolon-joined country string means a regional/multi-country
    # operation -- flag it explicitly rather than leave it silently mixed
    # into a single-country field. country_iso3 is left null for those (and
    # for "Regional"/"Not Defined") rather than guessed at which member is
    # "the" country.
    country_str = out["country"].fillna("").astype(str)
    out["is_regional"] = country_str.str.contains(";")
    out["country_iso3"] = out["country"].where(~out["is_regional"]).map(ISO3_BY_COUNTRY)
    out["project_name"] = _get("Project Name", first)
    sector = _get("Sector", first).fillna("")
    subsector = _get("Sub-Sector", first).fillna("")
    out["sector"] = (sector.astype(str) + " / " + subsector.astype(str)).str.strip(" /")
    code = _get("Modality Code", first).fillna("")
    out["modality"] = code.map(lambda c: KNOWN_MODALITY_CODES.get(str(c).strip(), str(c).strip()))

    total = pd.to_numeric(_get("Total Cost", first), errors="coerce")
    idb = idb_amt.reindex(out.index).fillna(0)
    cofin = cofin_amt.reindex(out.index).fillna(0)
    out["total_musd"] = (total / 1_000_000).round(2)
    out["idb_musd"] = (idb / 1_000_000).round(2)
    out["cofinancing_musd"] = (cofin / 1_000_000).round(2).where(cofin > 0)
    out["cofinancing_agency"] = cofin_agency.reindex(out.index)
    out["counterpart_musd"] = ((total - idb - cofin) / 1_000_000).round(2)

    out["borrower"] = _get("Borrower", first)
    out["executing_agency"] = _get("Executing Agency", first)
    out["env_category"] = _get("ESG Classification", first)
    lending_type = _get("Lending Type", first).fillna("")
    out["sovereign_guarantee"] = lending_type.astype(str).str.strip().eq("Sovereign Guaranteed")

    status = _get("Status", first).fillna("")
    out["bulk_status"] = status.astype(str).str.strip().str.title().replace("", None)
    approval_dt = pd.to_datetime(_get("Approval Date", first), errors="coerce")
    out["approval_date"] = approval_dt.dt.strftime("%Y-%m-%d")
    # No true cancellation date exists anywhere in the pipeline (Pipeline
    # Fixes Punch List, item 13) -- Last Updated is a proxy: whenever the
    # bulk record last changed, not necessarily the cancellation event
    # itself. Used only for terminal_state=="cancelled" rows in
    # build_project_month_panel.py, and only as a better-than-nothing
    # right-censoring point, not a validated cancellation date.
    last_updated_dt = pd.to_datetime(_get("Last Updated", first), errors="coerce")
    out["last_updated"] = last_updated_dt.dt.strftime("%Y-%m-%d")
    out["lending_instrument"] = code.map(lambda c: MODALITY_TO_VERIFIED_INSTRUMENT.get(str(c).strip()))

    out = out.reset_index(drop=True)
    return out.dropna(subset=["operation_number"]).drop_duplicates("operation_number")


def derive_terminal_state(hl_status, bulk_status):
    """Resolves harvest_log.csv's project_status (Closed/Cancelled/
    Implementation -- the only source with a real Cancelled category) against
    data.xlsx's bulk_status (EXITED/ACTIVE -- coarser, can't distinguish a
    normal completion from a cancellation) into one terminal_state used by
    the hazard model's competing-risks treatment of cancellation. harvest_log
    wins whenever it's populated; bulk_status is a fallback for the active/
    exited distinction only. An EXITED project with no harvest_log
    corroboration is left "unknown" rather than guessed -- see the Pipeline
    Fixes Punch List, item 3, for why this can't be resolved more precisely
    from what the pipeline currently captures."""
    # pandas hands missing cells back as float('nan'), which is TRUTHY, so
    # `x or ""` does not catch it and .strip() then blows up. Seen 2026-09-27 on
    # the full 2,581-operation panel, where some operations have no status in
    # harvest_log. Coerce anything that is not a string to "".
    def _s(x):
        return x.strip() if isinstance(x, str) else ""
    hl = _s(hl_status)
    bulk = _s(bulk_status)
    if hl == "Cancelled":
        return "cancelled"
    if hl == "Closed":
        return "disbursed_complete"
    if hl == "Implementation":
        return "still_active"
    if bulk == "Active":
        return "still_active"
    if bulk == "Exited":
        return "unknown"
    return "unknown"


def load_iati_dates(path: str) -> pd.DataFrame:
    """first_disbursement_date and totally_disbursed_date aren't in data.xlsx
    at all (Tenor/Guarantee Length is a different concept, and Disbursed
    Amount is a single snapshot, not a date) -- they only exist in the IATI
    general-project-details export, which covers 1,103 of this corpus's 2,581
    operations (confirmed 2026-09-21). Also pulls current_disbursement_
    expiration_date (useful as a right-censoring reference point) and IATI's
    own instrument type as iati_instrument_type, kept separate from (not
    merged into) lending_instrument -- a genuine cross-check between the two
    independently-sourced instrument labels is more useful than silently
    overwriting one with the other. Pipeline Fixes Punch List, item 4."""
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    raw.columns = [c.strip() for c in raw.columns]
    if "Operation number" not in raw.columns:
        return pd.DataFrame(columns=["operation_number"])
    raw = raw[raw["Operation number"].str.strip() != ""].copy()
    raw["operation_number"] = raw["Operation number"].str.strip()

    out = pd.DataFrame()
    out["operation_number"] = raw["operation_number"]
    for src_col, out_col in (
        ("First disbursement date", "first_disbursement_date"),
        ("Totally disbursed date", "totally_disbursed_date"),
        ("Current disbursement expiration date", "current_disbursement_expiration_date"),
    ):
        if src_col in raw.columns:
            dt = pd.to_datetime(raw[src_col], errors="coerce")
            out[out_col] = dt.dt.strftime("%Y-%m-%d")
        else:
            out[out_col] = None
    out["iati_instrument_type"] = raw["Instrument type"] if "Instrument type" in raw.columns else None

    return out.drop_duplicates("operation_number")


def load_panel(csv_path: str, harvest_log: str = None, data_xlsx: str = None,
                iati_project_details: str = None) -> pd.DataFrame:
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    for name, _, _ in FIELD_DEFS:
        if name in df.columns:
            df[name] = df[name].apply(lambda v: parse_cell(v, name))

    enriched_cols = []

    if data_xlsx and Path(data_xlsx).exists():
        n_before = len(df)
        meta = load_bulk_metadata(data_xlsx)
        meta_cols = [c for c in meta.columns if c != "operation_number"]
        df = df.merge(meta, on="operation_number", how="left")
        assert len(df) == n_before, (
            f"data.xlsx merge fanned out: {n_before} rows in -> {len(df)} rows out. "
            f"load_bulk_metadata() is supposed to already be deduplicated on "
            f"operation_number -- check for whitespace-inconsistent operation "
            f"numbers surviving drop_duplicates as distinct strings."
        )
        enriched_cols += meta_cols

    if harvest_log and Path(harvest_log).exists():
        log = pd.read_csv(harvest_log, dtype=str, keep_default_na=False)
        log.columns = [c.strip() for c in log.columns]
        # country/approval_date may already be filled in from --data-xlsx --
        # don't overwrite those or duplicate the column, just add what's new.
        # project_status is deliberately NOT excluded here even though
        # data.xlsx also has a status-like column: that column is named
        # bulk_status (see load_bulk_metadata), specifically so it can't
        # collide with harvest_log's project_status the way it used to.
        extra_cols = [c for c in ("country", "lending_instrument", "project_status", "approval_date")
                      if c in log.columns and c not in enriched_cols]
        if extra_cols:
            n_before = len(df)
            df = df.merge(log[["operation_number"] + extra_cols].drop_duplicates("operation_number"),
                           on="operation_number", how="left")
            assert len(df) == n_before, (
                f"harvest_log merge fanned out: {n_before} rows in -> {len(df)} rows out. "
                f"Check for whitespace-inconsistent or duplicate operation numbers in harvest_log.csv."
            )
            enriched_cols += extra_cols

    if iati_project_details and Path(iati_project_details).exists():
        iati = load_iati_dates(iati_project_details)
        iati_cols = [c for c in iati.columns if c != "operation_number"]
        n_before = len(df)
        df = df.merge(iati, on="operation_number", how="left")
        assert len(df) == n_before, (
            f"IATI general-project-details merge fanned out: {n_before} rows in -> {len(df)} rows out. "
            f"load_iati_dates() is supposed to already be deduplicated on operation_number."
        )
        enriched_cols += iati_cols

    if "project_status" in df.columns or "bulk_status" in df.columns:
        hl_col = df["project_status"] if "project_status" in df.columns else pd.Series([None] * len(df), index=df.index)
        bulk_col = df["bulk_status"] if "bulk_status" in df.columns else pd.Series([None] * len(df), index=df.index)
        df["terminal_state"] = [derive_terminal_state(h, b) for h, b in zip(hl_col, bulk_col)]
        if "terminal_state" not in enriched_cols:
            enriched_cols.append("terminal_state")

    if enriched_cols:
        # put the enriched columns right after operation_number
        ordered = ["operation_number"] + enriched_cols + [c for c in df.columns
                                                             if c not in enriched_cols and c != "operation_number"]
        df = df[ordered]
    return df


def load_audit(json_path: str) -> dict:
    with open(json_path, encoding="utf-8") as f:
        return json.load(f)


def style_header(ws, columns):
    for i, col in enumerate(columns, start=1):
        letter = get_column_letter(i)
        ws.column_dimensions[letter].width = COLUMN_WIDTHS.get(col, max(14, min(len(col) + 4, 34)))
        c = ws.cell(row=1, column=i, value=col)
        c.font = Font(name=FONT, size=10, bold=True, color=WHITE)
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        c.border = BORDER
    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"


SCHEMA_FIELD_NAMES = {name for name, _, _ in FIELD_DEFS}


def build_panel_sheet(wb: Workbook, df: pd.DataFrame):
    ws = wb.create_sheet("Microdata Panel")
    ws.sheet_view.showGridLines = False
    columns = list(df.columns)
    style_header(ws, columns)

    for r_i, row in enumerate(df.itertuples(index=False), start=2):
        for c_i, col in enumerate(columns, start=1):
            val = getattr(row, col) if hasattr(row, col) else row[c_i - 1]
            if val is None or (isinstance(val, float) and pd.isna(val)):
                # "not stated" is only a meaningful label for the actual
                # LLM-extracted schema fields -- it means the retrieved LP
                # text never addressed that field. For an enriched/merged
                # column (first_disbursement_date, country_iso3, etc.), blank
                # means "this project has no matching row in that external
                # source," a completely different kind of missingness that
                # was being mislabeled identically before this fix.
                label = "not stated" if col in SCHEMA_FIELD_NAMES else "no source match"
                cell = ws.cell(row=r_i, column=c_i, value=label)
                cell.font = Font(name=FONT, size=10, italic=True, color="808080")
                cell.fill = NULL_FILL
            elif isinstance(val, (bool, np.bool_)):
                # np.bool_ (what a merged-in pandas bool column yields, e.g.
                # sovereign_guarantee) isn't a Python bool subclass in
                # numpy -- without this it would fall through to the plain
                # text branch below instead of the colored Yes/No styling.
                cell = ws.cell(row=r_i, column=c_i, value="Yes" if val else "No")
                cell.font = Font(name=FONT, size=10)
                cell.fill = TRUE_FILL if val else FALSE_FILL
            else:
                cell = ws.cell(row=r_i, column=c_i, value=val)
                cell.font = Font(name=FONT, size=10)
                if (col.endswith("_pct") or col.endswith("_musd")) and isinstance(val, (int, float)):
                    cell.number_format = "0.0"
            cell.alignment = Alignment(wrap_text=True, vertical="top", horizontal="left")
            cell.border = BORDER

    note_row = len(df) + 3
    c = ws.cell(row=note_row, column=1,
                value="\"not stated\" = the field was not addressed in the retrieved LP text (not the same as a "
                      "confirmed absence -- see the Citations & Audit sheet for the found / stated_absent / "
                      "not_stated flag behind every cell); applies only to the LLM-extracted schema fields. "
                      "\"no source match\" = this column comes from an external bulk source (data.xlsx or the "
                      "IATI export), not LP text extraction, and this project has no matching row there -- not "
                      "a statement about the document. _pct columns are percentage points (64.3 = 64.3%), "
                      "not fractions.")
    c.font = Font(name=FONT, size=9, italic=True, color="595959")
    ws.merge_cells(start_row=note_row, start_column=1, end_row=note_row, end_column=len(columns))
    c.alignment = Alignment(wrap_text=True)
    return ws


def build_audit_sheet(wb: Workbook, audit: dict):
    ws = wb.create_sheet("Citations & Audit")
    ws.sheet_view.showGridLines = False
    columns = ["operation_number", "field", "value", "status", "citation", "quote"]
    widths = {"operation_number": 14, "field": 26, "value": 30, "status": 16, "citation": 20, "quote": 70}
    for i, col in enumerate(columns, start=1):
        letter = get_column_letter(i)
        ws.column_dimensions[letter].width = widths[col]
        c = ws.cell(row=1, column=i, value=col)
        c.font = Font(name=FONT, size=10, bold=True, color=WHITE)
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        c.border = BORDER
    ws.row_dimensions[1].height = 22
    ws.freeze_panes = "A2"

    r_i = 2
    for op_number in sorted(audit.keys()):
        for entry in audit[op_number].get("fields", []):
            values = [op_number, entry.get("field", ""), entry.get("value", ""), entry.get("status", ""),
                      entry.get("citation_para", ""), entry.get("quote", "")]
            for c_i, val in enumerate(values, start=1):
                cell = ws.cell(row=r_i, column=c_i, value=val if val is not None else "")
                cell.font = Font(name=FONT, size=9)
                cell.alignment = Alignment(wrap_text=True, vertical="top")
                cell.border = BORDER
            ws.cell(row=r_i, column=4).fill = PatternFill(
                "solid", fgColor=STATUS_COLORS.get(entry.get("status"), WHITE))
            r_i += 1
    return ws, r_i - 2  # number of audit rows written


def build_field_definitions_sheet(wb: Workbook):
    ws = wb.create_sheet("Field Definitions")
    ws.sheet_view.showGridLines = False
    columns = ["field", "type", "description"]
    widths = {"field": 30, "type": 20, "description": 90}
    for i, col in enumerate(columns, start=1):
        letter = get_column_letter(i)
        ws.column_dimensions[letter].width = widths[col]
        c = ws.cell(row=1, column=i, value=col)
        c.font = Font(name=FONT, size=10, bold=True, color=WHITE)
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        c.border = BORDER
    ws.freeze_panes = "A2"
    for r_i, (name, typ, desc) in enumerate(FIELD_DEFS, start=2):
        for c_i, val in enumerate([name, typ, desc], start=1):
            cell = ws.cell(row=r_i, column=c_i, value=val)
            cell.font = Font(name=FONT, size=10)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.border = BORDER
    caption = ws.cell(row=len(FIELD_DEFS) + 3, column=1,
                       value="Pulled directly from pipeline/schema.py, FIELD_DEFS -- edit the schema, "
                             "re-run this converter, and this sheet updates automatically.")
    caption.font = Font(name=FONT, size=9, italic=True, color="595959")
    ws.merge_cells(start_row=len(FIELD_DEFS) + 3, start_column=1, end_row=len(FIELD_DEFS) + 3, end_column=3)


def build_readme_sheet(wb: Workbook, df: pd.DataFrame, n_audit_rows: int, panel_path: str, audit_path: str):
    ws = wb.active
    ws.title = "Read Me"
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 100

    def rm(row, text, bold=False, size=11, color="000000", italic=False, top_pad=0):
        c = ws.cell(row=row, column=1, value=text)
        c.font = Font(name=FONT, size=size, bold=bold, color=color, italic=italic)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        return row + 1 + top_pad

    n = len(df)
    all_null = n > 0 and all(
        df[name].isna().all() if name in df.columns else True
        for name, _, _ in FIELD_DEFS
    )

    r = 1
    r = rm(r, "Loan Proposal Microdata Panel", bold=True, size=16, color=NAVY, top_pad=1)
    r = rm(r, f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} from {Path(panel_path).name} and "
              f"{Path(audit_path).name} by build_excel_report.py. {n} project(s), {n_audit_rows} audited "
              f"field-level citations.", top_pad=1)

    if all_null:
        r = rm(r, "WARNING: every extracted field in this run came back empty.", bold=True, size=12,
               color="C00000", top_pad=0)
        r = rm(r, "This is exactly what build_microdata_panel.py writes when ANTHROPIC_API_KEY isn't set -- "
                  "it runs Stage 1 (retrieval) only and fills every field with null so the panel's structure "
                  "can be checked before wiring in credentials. If you expected real values here, confirm the "
                  "key was exported in the same terminal session before re-running the extraction step.",
               top_pad=1)

    r = rm(r, "What's in this workbook", bold=True, size=12, color=NAVY, top_pad=0)
    has_bulk_meta = "project_name" in df.columns
    panel_desc = ("\"Microdata Panel\" -- one row per project, one column per schema field: the merge-ready "
                  "table for the duration-model panel, keyed by operation_number.")
    if has_bulk_meta:
        panel_desc += (" Leading columns (country, project name, sector, modality, financing amounts, "
                        "cofinancing agency/amount, borrower, executing agency, env. category, project status, "
                        "approval date, sovereign guarantee) came free from the bulk 'Download Project "
                        "Information' export (--data-xlsx), not from LP text extraction -- no citation exists "
                        "for them in the Citations & Audit sheet. cofinancing_musd/cofinancing_agency are only "
                        "populated where the export itself shows a second funding-source row for that project "
                        "(179 of 2,581 projects) -- blank does not necessarily mean there's no cofinancing, only "
                        "that none is visible in this particular export. lending_instrument is filled only for "
                        "modality codes verified against real scraped data (see build_excel_report.py).")
    r = rm(r, panel_desc, top_pad=0)
    r = rm(r, "\"Citations & Audit\" -- one row per project-field: the paragraph citation and verbatim quote "
              "behind every value in the panel, plus the status flag (found / stated_absent / not_stated), "
              "for spot-checking against the source PDFs.", top_pad=0)
    r = rm(r, f"\"Field Definitions\" -- a glossary of the {len(FIELD_DEFS)} schema fields, pulled directly "
              f"from schema.py.",
           top_pad=1)
    r = rm(r, "How this was produced", bold=True, size=12, color=NAVY, top_pad=0)
    r = rm(r, "harvest_projects.py found and downloaded each project's loan proposal package; "
              "parse_loan_proposal.py split it into cited paragraphs and retrieved the financing/procurement/"
              "risk/execution sections; build_microdata_panel.py sent those to Claude with a forced tool call "
              "per field, returning a citation, a verbatim quote, and a found/stated_absent/not_stated flag; "
              "this script assembled the result into one workbook.", top_pad=0)

    return ws


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", required=True, help="microdata_panel.csv from build_microdata_panel.py")
    ap.add_argument("--audit", required=True, help="microdata_audit.json from build_microdata_panel.py")
    ap.add_argument("--out", required=True)
    ap.add_argument("--harvest-log", default=None, help="optional harvest_log.csv to add country/lending "
                                                           "instrument/approval date columns")
    ap.add_argument("--data-xlsx", default=None, help="optional bulk 'Download Project Information' export "
                                                         "(data.xlsx) to add country/country_iso3/is_regional/"
                                                         "project name/sector/modality/financing amounts/"
                                                         "cofinancing agency & amount/borrower/executing agency/"
                                                         "env category/bulk_status/approval date/sovereign "
                                                         "guarantee columns -- all free, no extra API calls")
    ap.add_argument("--iati-project-details", default=None,
                     help="optional idb-iati-dataset-general-project-details.csv to add "
                          "first_disbursement_date/totally_disbursed_date/"
                          "current_disbursement_expiration_date/iati_instrument_type columns "
                          "(covers 1,103 of 2,581 projects in this corpus)")
    args = ap.parse_args()

    df = load_panel(args.panel, args.harvest_log, args.data_xlsx, args.iati_project_details)
    audit = load_audit(args.audit)

    wb = Workbook()
    build_panel_sheet(wb, df)
    _, n_audit_rows = build_audit_sheet(wb, audit)
    build_field_definitions_sheet(wb)
    build_readme_sheet(wb, df, n_audit_rows, args.panel, args.audit)  # must be last: reuses wb.active (sheet 0)

    # openpyxl creates sheets in call order but "Read Me" should be first
    wb.move_sheet("Read Me", offset=-(len(wb.sheetnames) - 1))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    wb.save(args.out)
    print(f"Wrote {args.out}: {len(df)} project(s), {n_audit_rows} audit rows, "
          f"sheets={wb.sheetnames}")


if __name__ == "__main__":
    main()
