#!/usr/bin/env python3
"""
Pipeline Fixes Punch List, item 7: draws a random sample of extracted
field-values for independent hand-coding, so the "validate a random
subsample against hand-coding" robustness check the methodology notes commit
to (Section on measurement error in document-extracted shares, both
Methodology_Hazard_LP_ShiftShare.tex and the extensive-margin companion) is
actually runnable, not just planned.

Sampling is stratified by status (found / stated_absent / not_stated) rather
than a flat random draw across all ~17,000 field-values in
microdata_audit.json -- a flat sample would be dominated by "not_stated"
rows for fields that are rarely discussed (easy, uninformative to review),
undersampling the "found" rows where a genuine transcription/interpretation
error is possible. --n-per-status controls how many of each status to draw
per field.

Usage:
    python3 sample_for_validation.py --audit microdata_audit.json \
        --corpus-root ../corpus --n-per-status 5 \
        --out ../output/validation_sample.xlsx

Then: open the output, fill in "hand_coded_value" and "reviewer_notes" for
each row (open the cited document at the citation_para/quote to check), and
run score_validation.py on the completed file.
"""
import argparse
import json
import random
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

FONT = "Arial"
NAVY = "1F3864"
WHITE = "FFFFFF"
GREY = "F2F2F2"
YELLOW = "FFF2CC"
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

COLUMNS = ["operation_number", "field", "status", "extracted_value", "citation_para",
           "quote", "hand_coded_value", "agrees (Y/N)", "reviewer_notes"]
COLUMN_WIDTHS = {"operation_number": 14, "field": 26, "status": 16, "extracted_value": 26,
                  "citation_para": 16, "quote": 60, "hand_coded_value": 26,
                  "agrees (Y/N)": 14, "reviewer_notes": 40}


def load_audit_rows(audit_path: str):
    with open(audit_path, encoding="utf-8") as f:
        audit = json.load(f)
    rows, malformed = [], 0
    for op_number, entry in audit.items():
        for c in entry.get("fields", []):
            # A forced tool call occasionally comes back missing arguments. An
            # entry with no "status" cannot be stratified and has nothing for a
            # reviewer to check, and a None key made the sort in
            # stratified_sample() raise TypeError comparing None to str.
            # 3 such entries in the 2026-09-27 audit (BR-L1160, BR-L1545,
            # PR-L1055), out of roughly 41,500.
            if not isinstance(c, dict) or c.get("field") is None or c.get("status") is None:
                malformed += 1
                continue
            rows.append({
                "operation_number": op_number, "field": c.get("field"),
                "status": c.get("status"), "extracted_value": c.get("value"),
                "citation_para": c.get("citation_para"), "quote": c.get("quote"),
            })
    if malformed:
        print(f"[note] skipped {malformed} field entr{'y' if malformed==1 else 'ies'} with no "
              f"field/status recorded (malformed tool calls) -- not sampleable",
              file=sys.stderr)
    return rows


def stratified_sample(rows: list, n_per_status: int, seed: int):
    rng = random.Random(seed)
    by_field_status = {}
    for r in rows:
        key = (r["field"], r["status"])
        by_field_status.setdefault(key, []).append(r)

    sampled = []
    for key, group in sorted(by_field_status.items()):
        k = min(n_per_status, len(group))
        sampled.extend(rng.sample(group, k))
    return sampled


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit", default="microdata_audit.json")
    ap.add_argument("--n-per-status", type=int, default=5,
                     help="how many rows to sample per (field, status) combination (default 5)")
    ap.add_argument("--out", default="../output/validation_sample.xlsx")
    ap.add_argument("--seed", type=int, default=20260922, help="for reproducible sampling")
    args = ap.parse_args()

    if not Path(args.audit).exists():
        print(f"[error] {args.audit} not found", file=sys.stderr)
        sys.exit(1)

    rows = load_audit_rows(args.audit)
    sampled = stratified_sample(rows, args.n_per_status, args.seed)
    print(f"Sampled {len(sampled)} of {len(rows)} total field-values "
          f"({args.n_per_status} per field x status combination, seed={args.seed})",
          file=sys.stderr)

    wb = Workbook()
    ws = wb.active
    ws.title = "Validation Sample"
    ws.sheet_view.showGridLines = False

    for c_i, col in enumerate(COLUMNS, start=1):
        letter = get_column_letter(c_i)
        ws.column_dimensions[letter].width = COLUMN_WIDTHS.get(col, 20)
        cell = ws.cell(row=1, column=c_i, value=col)
        cell.font = Font(name=FONT, size=10, bold=True, color=WHITE)
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        cell.border = BORDER
    ws.freeze_panes = "A2"

    for r_i, row in enumerate(sampled, start=2):
        values = [row["operation_number"], row["field"], row["status"],
                  row["extracted_value"], row["citation_para"], row["quote"],
                  None, None, None]
        for c_i, val in enumerate(values, start=1):
            cell = ws.cell(row=r_i, column=c_i, value=val)
            cell.font = Font(name=FONT, size=10)
            cell.alignment = Alignment(wrap_text=True, vertical="top", horizontal="left")
            cell.border = BORDER
            if COLUMNS[c_i - 1] in ("hand_coded_value", "agrees (Y/N)", "reviewer_notes"):
                cell.fill = PatternFill("solid", fgColor=YELLOW)

    readme = wb.create_sheet("Read Me", 0)
    readme.sheet_view.showGridLines = False
    readme.column_dimensions["A"].width = 100
    lines = [
        ("How to use this sheet", True, 12),
        (f"This is a stratified random sample of {len(sampled)} extracted field-values "
         f"(out of {len(rows)} total), {args.n_per_status} per (field, status) combination, "
         "drawn from microdata_audit.json.", False, 10),
        ("For each row: open the cited source document for that operation_number in the "
         "corpus folder, find the citation_para/quote, and independently read off what the "
         "field's true value should be. Fill in hand_coded_value with your independent read, "
         "agrees (Y/N) with whether it matches extracted_value, and reviewer_notes with "
         "anything worth flagging (ambiguous wording, a citation that doesn't support the "
         "value, etc.).", False, 10),
        ("Leave hand_coded_value blank for any row you haven't gotten to yet -- "
         "score_validation.py treats a blank hand_coded_value as 'not yet reviewed' and "
         "excludes it from the agreement-rate calculation, so partial progress is safe to "
         "score at any point.", False, 10),
        ("When done (or partially done), run: python3 score_validation.py --sample "
         f"{Path(args.out).name}", False, 10),
    ]
    r = 1
    for text, bold, size in lines:
        cell = readme.cell(row=r, column=1, value=text)
        cell.font = Font(name=FONT, size=size, bold=bold, color=NAVY if bold else "000000")
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        readme.row_dimensions[r].height = 18 if bold else 44
        r += 2

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    wb.save(args.out)
    print(f"Wrote {args.out}: {len(sampled)} rows to review")


if __name__ == "__main__":
    main()
