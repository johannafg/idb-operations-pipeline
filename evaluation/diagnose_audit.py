"""
Diagnose extraction coverage from the audit log.

Separates four failure mechanisms that a plain fill-rate table conflates:
  1. parsing failure    - no paragraphs recovered from the document at all
  2. retrieval gap      - paragraphs parsed, but none tagged relevant
  3. documentary silence vs. retrieval miss - read from the status flag
  4. truncation         - bundle hit the character cap

Usage:
    python3 evaluation/diagnose_audit.py [path/to/microdata_audit.json]
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

AUDIT = Path(sys.argv[1]) if len(sys.argv) > 1 else \
    Path(__file__).resolve().parent.parent / "data" / "audit" / "microdata_audit.json"

# English-language borrowers, used as a natural comparison for the
# English-only retrieval keywords in src/parse_loan_proposal.py.
ENGLISH = {"BA", "BH", "BL", "GY", "JA", "TT"}
BUNDLE_CAP = 24000


def pct(a, b):
    return 100.0 * a / b if b else 0.0


def quantile(xs, p):
    s = sorted(xs)
    return s[int(p * (len(s) - 1))]


def main():
    audit = json.loads(AUDIT.read_text())
    ops = list(audit.items())
    n = len(ops)
    print(f"Operations in audit log: {n}\n")

    # --- 1. status by field -------------------------------------------------
    by_field = defaultdict(Counter)
    for _, v in ops:
        for f in v.get("fields", []):
            by_field[f["field"]][f["status"]] += 1
    print("FIELD STATUS")
    print(f"{'field':32s} {'found':>6s} {'absent':>7s} {'not_st':>7s} {'found%':>7s}")
    for field, c in by_field.items():
        print(f"{field:32s} {c['found']:6d} {c['stated_absent']:7d} "
              f"{c['not_stated']:7d} {pct(c['found'], n):6.1f}%")
    print()

    # --- 2. parsing and retrieval health ------------------------------------
    tot = [v["stats"].get("n_paragraphs_total", 0) for _, v in ops]
    rel = [v["stats"].get("n_paragraphs_relevant", 0) for _, v in ops]
    bc = [v["stats"].get("bundle_chars", 0) for _, v in ops]
    print("PARSING AND RETRIEVAL")
    for name, xs in [("paragraphs parsed", tot), ("paragraphs relevant", rel),
                     ("bundle characters", bc)]:
        print(f"  {name:22s} p25={quantile(xs,.25):6d}  median={quantile(xs,.5):6d}  "
              f"p75={quantile(xs,.75):6d}  max={max(xs):6d}")
    zero_parsed = sum(1 for x in tot if x == 0)
    zero_rel = sum(1 for x in rel if x == 0)
    capped = sum(1 for x in bc if x >= BUNDLE_CAP)
    print(f"  operations with 0 paragraphs parsed   : {zero_parsed:4d} ({pct(zero_parsed,n):4.1f}%)")
    print(f"  operations with 0 relevant paragraphs : {zero_rel:4d} ({pct(zero_rel,n):4.1f}%)")
    print(f"  operations at the {BUNDLE_CAP:,}-char cap  : {capped:4d} ({pct(capped,n):4.1f}%)")
    print()

    # --- 3. ingestion failures ----------------------------------------------
    failed = sum(v["stats"].get("n_documents_failed", 0) for _, v in ops)
    ops_failed = sum(1 for _, v in ops if v["stats"].get("n_documents_failed", 0))
    print("INGESTION")
    print(f"  documents that failed to open: {failed} across {ops_failed} operations\n")

    # --- 4. language comparison ---------------------------------------------
    print("RETRIEVAL BY BORROWER LANGUAGE")
    for label, eng in [("English-language", True), ("Spanish/Portuguese", False)]:
        g = [(op, v) for op, v in ops if (op.split("-")[0] in ENGLISH) == eng]
        m = len(g)
        found = sum(1 for _, v in g for f in v.get("fields", []) if f["status"] == "found")
        r = sum(v["stats"].get("n_paragraphs_relevant", 0) for _, v in g)
        t = sum(v["stats"].get("n_paragraphs_total", 0) for _, v in g)
        z = sum(1 for _, v in g if v["stats"].get("n_paragraphs_relevant", 0) == 0)
        print(f"  {label:20s} n={m:5d}  relevant/parsed={pct(r,t):5.1f}%  "
              f"fields found per op={found/m:4.2f}  zero-relevant={pct(z,m):4.1f}%")


if __name__ == "__main__":
    main()
