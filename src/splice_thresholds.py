"""Recompute John's 25/50/75 percent disbursement milestones with three extra
years of history from the IATI registry.

Free. No API calls, no network.

    cd ~/Library/CloudStorage/OneDrive-Personal/IDB_Loans/pipeline_merged
    python3 ../_claude_temp/threshold_splice/splice_thresholds.py

METHOD, and why it is built this way.

John's definition (Project Database v2.do, sections 4A/4B):

    share      = 100 * cumulative signed DISB / Amount_Approved   (original approved)
    DISB_xx_DT = first MONTH in which share >= xx

The warehouse cashflow stops in October 2023, which leaves 9.4% of operations
short of 50% and 21.5% short of 75%. The registry runs to August 2026.

The splice takes the warehouse's own cumulative at the cutoff as the anchor and
appends ONLY registry flows dated after it. The registry never has to agree with
the warehouse about history -- only about what happened after the warehouse
stopped looking -- so every pre-cutoff month stays exactly as EDW recorded it.
Validated on the 2004-2018 overlap: 98% agreement within one month at all three
thresholds.

Registry months are shifted +1 to match EDW's accounting convention. IATI stamps
a transaction on the last day of its month; EDW posts it in that month or the
next depending on the accounting close, with the mode on the next. Without the
shift 93 operations are recovered at 75% instead of 100, so the conclusion does
not turn on it, but the shift is the better alignment.

Every operation carries a `spliced` flag and the number of appended months, so
the specification can be run both ways.
"""
import pandas as pd, numpy as np, os, warnings
warnings.filterwarnings("ignore")

BASE = os.path.expanduser("~/Library/CloudStorage/OneDrive-Personal/IDB_Loans")
if not os.path.isdir(BASE):
    BASE = os.path.join(os.path.expanduser("~"), "mnt", "IDB_Loans")
OUT = os.path.join(BASE, "_claude_temp", "threshold_splice")

CUTOFF = pd.Period("2023-10", "M")
SHIFT = 1
THRESHOLDS = (25, 50, 75)


def stata_month(s):
    x = s.astype(str).str.extract(r"^(\d{4})m(\d{1,2})$"); ok = x[0].notna()
    o = pd.Series(pd.NaT, index=s.index, dtype="period[M]")
    o[ok] = pd.PeriodIndex.from_fields(year=x.loc[ok, 0].astype(int),
                                       month=x.loc[ok, 1].astype(int), freq="M")
    return o


def main():
    # ---- 1. the warehouse monthly stream, up to the cutoff ----
    md = pd.read_stata(os.path.join(BASE, "inputs", "Clean_Project_Month_Disbursements.dta"),
                       convert_categoricals=False)
    md["m"] = pd.PeriodIndex(pd.to_datetime(md.accounting_disb_date), freq="M")
    pre = md[md.m <= CUTOFF].copy()
    anchor = pre.sort_values("m").groupby("OPER_NUM").agg(
        cum_cut=("cum_disb_signed", "last"), last_month=("m", "last"))
    print(f"warehouse stream: {len(md):,} rows, {md.OPER_NUM.nunique():,} operations")
    print(f"  months at or before {CUTOFF}: {len(pre):,}")

    # ---- 2. operation-level denominators and John's own milestones ----
    e = (pd.read_csv(os.path.join(BASE, "inputs", "loan_data.csv"), low_memory=False)
           .drop_duplicates("OPER_NUM").copy())
    e["elig"] = pd.to_datetime(e["FRST_ELIG_DT"], format="%d%b%Y", errors="coerce")
    for t in THRESHOLDS:
        e[f"DISB_{t}_DT"] = stata_month(e[f"DISB_{t}_DT"])
    ops = e.set_index("OPER_NUM")

    # ---- 3. registry flows after the cutoff ----
    reg = pd.read_csv(os.path.join(BASE, "_claude_temp", "iati_registry",
                                   "iati_registry_transactions.csv"), low_memory=False)
    reg = reg[reg.transaction_type == "DISBURSEMENT"].copy()
    reg["m"] = pd.PeriodIndex(pd.to_datetime(reg.date), freq="M") + SHIFT
    post = reg[reg.m > CUTOFF]
    flows = (post.groupby(["operation_number", "m"], as_index=False)["amount"].sum()
                 .sort_values(["operation_number", "m"]))
    print(f"registry post-cutoff: {len(flows):,} operation-months, "
          f"{flows.operation_number.nunique():,} operations")

    # ---- 4. splice and recompute ----
    f = flows.join(anchor, on="operation_number")
    f = f.join(ops["Amount_Approved"], on="operation_number")
    f = f.dropna(subset=["cum_cut", "Amount_Approved"])
    f = f[f.Amount_Approved > 0]
    f["cum"] = f.cum_cut + f.groupby("operation_number")["amount"].cumsum()
    f["share"] = 100 * f["cum"] / f["Amount_Approved"]

    rows = []
    for op in ops.index:
        r = {"OPER_NUM": op}
        g = f[f.operation_number == op] if op in set(f.operation_number) else None
        r["spliced"] = g is not None and len(g) > 0
        r["n_spliced_months"] = 0 if g is None else len(g)
        r["last_spliced_month"] = None if g is None or not len(g) else str(g.m.max())
        r["share_at_cutoff"] = (round(100 * anchor.cum_cut.get(op, np.nan) /
                                      ops.Amount_Approved.get(op, np.nan), 2)
                                if op in anchor.index and ops.Amount_Approved.get(op, 0) > 0
                                else None)
        r["share_after_splice"] = None if g is None or not len(g) else round(g.share.max(), 2)
        for t in THRESHOLDS:
            orig = ops.at[op, f"DISB_{t}_DT"]
            r[f"DISB_{t}_DT_orig"] = None if pd.isna(orig) else str(orig)
            new = orig
            if pd.isna(orig) and g is not None and len(g):
                hit = g.loc[g.share >= t, "m"]
                if len(hit):
                    new = hit.min()
            r[f"DISB_{t}_DT_spliced"] = None if pd.isna(new) else str(new)
            r[f"recovered_{t}"] = bool(pd.isna(orig) and not pd.isna(new))
        rows.append(r)
    out = pd.DataFrame(rows)

    p = os.path.join(BASE, "output", "disbursement_thresholds_spliced.csv")
    out.to_csv(p, index=False)

    # ---- 5. report ----
    win = e[e.elig >= "1996-01-01"].OPER_NUM
    w = out[out.OPER_NUM.isin(win)]
    print(f"\n=== estimation window: eligibility >= 1996, {len(w):,} operations ===")
    print(f"{'thr':>4} {'censored before':>15} {'recovered':>10} {'censored after':>15} "
          f"{'rate before':>12} {'rate after':>11}")
    for t in THRESHOLDS:
        before = w[f"DISB_{t}_DT_orig"].isna().sum()
        rec = w[f"recovered_{t}"].sum()
        after = before - rec
        print(f"{t:>3}% {before:>15} {rec:>10} {after:>15} "
              f"{before/len(w)*100:>11.1f}% {after/len(w)*100:>10.1f}%")
    print(f"\noperations with appended months: {w.spliced.sum():,}")
    print(f"  median months appended: {w.loc[w.spliced,'n_spliced_months'].median():.0f}")
    over = w[(w.share_after_splice.notna()) & (w.share_after_splice > 105)]
    print(f"  ending above 105% of approved (double-count check): {len(over)}")
    print(f"\nwrote {p}")
    print("  columns: DISB_xx_DT_orig, DISB_xx_DT_spliced, recovered_xx, spliced,")
    print("           n_spliced_months, share_at_cutoff, share_after_splice")
    print("  merge on OPER_NUM. Use _orig for the baseline specification and")
    print("  _spliced for the extended one; `spliced` identifies the operations")
    print("  where the two can differ.")


if __name__ == "__main__":
    main()
