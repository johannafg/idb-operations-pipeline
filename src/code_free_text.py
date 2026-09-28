"""Code the three free-text extraction fields into analysis-ready variables.

Free. Deterministic. No API calls. Reads the panel, writes a coded companion
file; the panel itself is not modified.

    cd ~/Library/CloudStorage/OneDrive-Personal/IDB_Loans/pipeline_merged
    python3 ../_claude_temp/freetext_coding/code_free_text.py

WHY: executing_agency_type, fx_denomination and procurement_modality come back
as the document's own words. They agree on substance across runs but vary in
wording ("subnational government secretariat" against "subnational government
department" for the same operation), so as strings they cannot enter a
regression. 1,998 distinct values across 2,438 populated cells for the agency
field alone.

DESIGN DECISION worth reviewing before you use these. None of the three is a
single category:

  executing_agency_type carries three separable facts -- the LEVEL of
  government, the institutional FORM, and whether the executing unit was NEWLY
  CREATED for this operation. The last is the interesting one: a unit built from
  scratch plausibly disburses more slowly than a standing ministry, and 386 of
  the extracted values say "created" while 175 say "existing". Collapsing all
  three into one category would throw that away.

  procurement_modality is MULTI-LABEL. An operation typically lists several
  regimes (ICB and NCB and shopping). One categorical variable cannot hold that,
  so this produces one indicator per regime plus a count.

  fx_denomination is nearly always US dollars; what varies is the facility and
  whether local-currency conversion is available. The useful output is therefore
  a small set of flags rather than a currency name.

Patterns are bilingual (English/Spanish) plus some Portuguese, because the
corpus is. Every unmatched value is written to an "unmatched" report so the
residual is visible rather than silently swept into "other".
"""
import pandas as pd, numpy as np, re, os, sys, warnings
warnings.filterwarnings("ignore")

BASE = os.path.expanduser("~/Library/CloudStorage/OneDrive-Personal/IDB_Loans")
if not os.path.isdir(BASE):
    BASE = os.path.join(os.path.expanduser("~"), "mnt", "IDB_Loans")
OUT = os.path.join(BASE, "_claude_temp", "freetext_coding")


def _n(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = str(v).strip().lower()
    if not s or s in {"nan", "none", "not stated", "no source match"}:
        return None
    return s


def has(s, *pats):
    return any(re.search(p, s) for p in pats)


# ---------------------------------------------------------------- agency ---
def agency_level(s):
    if s is None: return None
    if has(s, r"\bmunicipal", r"\bmunicip", r"\bcity\b", r"\bprefeitura"):
        return "municipal"
    if has(s, r"\bsubnational", r"\bprovincial", r"\bstate government", r"\bstate of\b",
              r"\bdepartamental", r"\bregional government", r"\bgobierno provincial",
              r"\bestado de\b", r"\bprovince"):
        return "subnational"
    if has(s, r"\bnational\b", r"\bfederal\b", r"\bcentral government", r"\bnacional\b",
              r"\bministry\b", r"\bministerio", r"\bministério"):
        return "national"
    if has(s, r"\bregional\b", r"\bmultinational", r"\bsupranational"):
        return "regional"
    # The document names an executing body but not its level of government --
    # common for autonomous enterprises and financial intermediaries, where the
    # level genuinely is not the salient fact. Distinguished from a missing value.
    return "level_not_stated"


def agency_form(s):
    if s is None: return None
    if has(s, r"\bfinancial intermediar", r"\bsecond[- ]tier", r"\bdevelopment bank",
              r"\btrust fund\b", r"\bbanco de", r"\bbank\b.*\b(develop|export|comercio)"):
        return "financial_intermediary"
    if has(s, r"\bstate[- ]owned", r"\bsoe\b", r"\bautonomous\b", r"\bpublic enterprise",
              r"\bempresa p[uú]blica", r"\bautarqu", r"\bdecentralised\b", r"\bdecentralized\b"):
        return "autonomous_agency_soe"
    if has(s, r"\bministry\b", r"\bministerio", r"\bministério", r"\bsecretariat\b",
              r"\bsecretar[ií]a", r"\bdepartment\b"):
        return "ministry_or_secretariat"
    if has(s, r"\bproject (execution|management|coordination) unit", r"\bexecuting unit",
              r"\bunidad ejecutora", r"\bunidade executora", r"\bpmu\b", r"\bpiu\b",
              r"\b(coordination|coordinating|execution)\b.{0,30}\bunit\b",
              r"\bunit\b.{0,30}\b(coordination|execution)\b",
              r"\bunidad de (coordinaci[oó]n|ejecuci[oó]n)", r"\bsub-?executing\b",
              r"\bexecution unit", r"\bsub[- ]?executing agenc"):
        return "project_unit"
    if has(s, r"\blegislative\b", r"\bjudicial\b", r"\bcomptroller\b", r"\bsenate\b",
              r"\bcongress\b", r"\belectoral\b"):
        return "other_branch_of_state"
    return "other"


def agency_newly_created(s):
    """True when the text says the executing unit was created for this operation."""
    if s is None: return None
    if has(s, r"\bnewly created", r"\bnewly[- ]established", r"\bto be created",
              r"\bwill be created", r"\bnueva\b.*\bunidad", r"\bde nueva creaci[oó]n",
              r"\bcreated for (this|the) (program|project|operation)", r"\ba ser creada"):
        return True
    if has(s, r"\bexisting\b", r"\balready (exists|established)", r"\bnot newly",
              r"\bexistente\b", r"\bya existente"):
        return False
    return None


# -------------------------------------------------------------------- fx ---
def fx_flags(s):
    if s is None: return {}
    return {
        "fx_usd": has(s, r"\bu\.?s\.?a?\.?\s*dollar", r"\bdollars? of the united states",
                         r"\bd[oó]lar", r"\busd\b", r"\bunited states dollar",
                         r"us\s*\$", r"\bus\$", r"united states of americ\w* dollar"),
        "fx_single_currency_facility": has(s, r"single currency facility", r"\bscf\b",
                                              r"facilidad de moneda [uú]nica"),
        "fx_ordinary_capital": has(s, r"ordinary capital", r"capital ordinario", r"\boc\b"),
        "fx_concessional": has(s, r"fund for special operations", r"\bfso\b",
                                  r"fondo de operaciones especiales", r"concessional"),
        "fx_local_currency_option": has(s, r"local currency", r"moneda local",
                                           r"conversion to local", r"convertible a moneda"),
    }


# ----------------------------------------------------------- procurement ---
PROC = {
    "proc_icb": (r"\bicb\b", r"international competitive bidding",
                 r"licitaci[oó]n p[uú]blica internacional", r"\blpi\b"),
    "proc_ncb": (r"\bncb\b", r"national competitive bidding",
                 r"licitaci[oó]n p[uú]blica nacional", r"\blpn\b"),
    "proc_shopping": (r"\bshopping\b", r"comparison of prices", r"price comparison",
                      r"comparaci[oó]n de precios", r"compara[cç][aã]o de pre[cç]os"),
    "proc_direct": (r"direct contracting", r"contrataci[oó]n directa", r"sole source",
                    r"single source", r"selecci[oó]n directa"),
    "proc_consultants": (r"individual consultant", r"consultor(es)? individual",
                         r"\bqcbs\b", r"quality[- ]and[- ]cost", r"\bsbcc\b",
                         r"selection based on quality"),
    "proc_force_account": (r"force account", r"administraci[oó]n directa", r"por administraci[oó]n"),
    "proc_national_law": (r"national procurement (law|act|system)", r"ley de contrataci",
                          r"public procurement act", r"r[eé]gimen nacional",
                          r"private sector procurement", r"pr[aá]cticas del sector privado",
                          r"national systems?\b", r"procedimientos nacionales",
                          r"sistemas? nacional", r"national procedures"),
    # A bare "licitación pública" or "competitive bidding" with no nationality
    # qualifier, or the Bank's own standard procedures. Kept separate so it is
    # never mistaken for a confirmed ICB or NCB.
    "proc_bidding_unspecified": (r"^licitaci[oó]n p[uú]blica\s*$", r"\bbank standard procedures",
                                 r"\bmarket procurement practices", r"\bcompetitive bidding\b",
                                 r"\blocal competitive bidding", r"\bpr[aá]cticas comerciales"),
}


def proc_flags(s):
    if s is None: return {}
    f = {k: has(s, *pats) for k, pats in PROC.items()}
    # "competitive bidding" appears inside "international competitive bidding",
    # so the unspecified flag would double-count every ICB/NCB operation and
    # inflate the modality count. It means only: bidding is mentioned but its
    # nationality is not.
    if f["proc_icb"] or f["proc_ncb"]:
        f["proc_bidding_unspecified"] = False
    f["proc_n_modalities"] = sum(bool(v) for k, v in f.items() if k != "proc_n_modalities")
    return f


def main():
    panel = os.path.join(BASE, "output", "microdata_panel_full.csv")
    d = pd.read_csv(panel, low_memory=False)
    out = pd.DataFrame({"operation_number": d.operation_number})

    a = d.get("executing_agency_type", pd.Series(dtype=object)).map(_n)
    out["agency_level"] = a.map(agency_level)
    out["agency_form"] = a.map(agency_form)
    out["agency_newly_created"] = a.map(agency_newly_created)

    fx = d.get("fx_denomination", pd.Series(dtype=object)).map(_n)
    for k in ("fx_usd","fx_single_currency_facility","fx_ordinary_capital",
              "fx_concessional","fx_local_currency_option"):
        out[k] = fx.map(lambda s, k=k: fx_flags(s).get(k))

    pr = d.get("procurement_modality", pd.Series(dtype=object)).map(_n)
    for k in list(PROC) + ["proc_n_modalities"]:
        out[k] = pr.map(lambda s, k=k: proc_flags(s).get(k))

    p = os.path.join(BASE, "output", "coded_free_text.csv")
    out.to_csv(p, index=False)

    # ---- report ----
    print(f"wrote {p}  ({len(out)} rows, {len(out.columns)-1} coded variables)\n")
    print("=== executing_agency_type ===")
    for c in ("agency_level","agency_form"):
        print(f"\n{c}:")
        print(out[c].value_counts(dropna=False).to_string())
    print("\nagency_newly_created:")
    print(out.agency_newly_created.value_counts(dropna=False).to_string())

    print("\n=== fx_denomination (share of populated) ===")
    n = fx.notna().sum()
    for c in ("fx_usd","fx_single_currency_facility","fx_ordinary_capital",
              "fx_concessional","fx_local_currency_option"):
        t = (out[c] == True).sum()
        print(f"  {c:32s} {t:5d}  ({t/n*100:5.1f}% of {n})")

    print("\n=== procurement_modality (share of populated) ===")
    n = pr.notna().sum()
    for c in PROC:
        t = (out[c] == True).sum()
        print(f"  {c:22s} {t:5d}  ({t/n*100:5.1f}% of {n})")
    print(f"  modalities per operation: mean "
          f"{out.loc[pr.notna(),'proc_n_modalities'].mean():.2f}, "
          f"none matched for {(out.loc[pr.notna(),'proc_n_modalities']==0).sum()}")

    # ---- unmatched residuals, so nothing hides in "other" ----
    res = []
    for label, series, mask in (
        ("agency_form=other", a, out.agency_form == "other"),
        ("agency_level=other", a, out.agency_level == "other"),
        ("fx: no flag set", fx, fx.notna() & ~out[["fx_usd","fx_single_currency_facility",
             "fx_ordinary_capital","fx_concessional","fx_local_currency_option"]].any(axis=1)),
        ("procurement: no flag set", pr, pr.notna() & (out.proc_n_modalities == 0)),
    ):
        for op, v in zip(out.operation_number[mask], series[mask]):
            res.append({"check": label, "operation_number": op, "raw_value": v})
    r = pd.DataFrame(res)
    rp = os.path.join(OUT, "unmatched_values.csv")
    r.to_csv(rp, index=False)
    print(f"\nunmatched values written to {rp}  ({len(r)} rows)")
    if len(r):
        print(r.check.value_counts().to_string())


if __name__ == "__main__":
    main()
