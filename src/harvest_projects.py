"""
Stage 0: harvesting agent -- finds candidate IDB operations and downloads
their Loan Proposal package (main document + annexes) into corpus/<op>/,
matching exactly the input layout parse_loan_proposal.py / build_microdata_panel.py
already expect.

Verified directly (by fetching real pages, not assumed):
  - https://www.iadb.org/en/project/<OPERATION-NUMBER> is public, unauthenticated,
    and lists every document attached to the project, grouped by phase, with a
    document-type label per entry -- "Loan Proposal" is used as the category for
    the LP itself AND every annex (technical annex, economic analysis, gender
    note, institutional strengthening plan, procurement plan, etc.), so filtering
    on that one label collects the whole package in one pass.
  - The project page also lists Country, Approval Date, Project Status, and
    Lending Instrument (e.g. "Investment Loan"), which is exactly what's needed
    to apply the paper's sample filter without an internal/authenticated data
    source.

The project list comes from the search page's "Download Project Information
(.xlsx)" button, passed with --project-list, or from an explicit list of
operation numbers passed with --only-operations. Both converge on the same
per-project download logic below.

A third mode once paginated the public search table directly. It was removed on
28 September 2026: it raised against the live site, and it had produced none of
the corpus.

Usage:
    # Mode A: filter an official bulk export you downloaded
    python3 harvest_projects.py --project-list "Project Information.xlsx" \
        --corpus-root ../corpus --start-year 1998 --end-year 2026

    # Mode B: harvest an explicit list of operation numbers
    python3 harvest_projects.py --only-operations ops.txt --corpus-root ../corpus

--end-year defaults to the current year (see DEFAULT_END_YEAR below), not a
fixed cutoff -- 2026-08-14: switched away from a hardcoded --end-year 2019
after deciding to keep the wider approval-year sample for the duration
model and handle COVID-era disbursement disruption via an explicit
exposure covariate at the panel-construction stage instead of truncating
the sample by approval year (which doesn't even cleanly exclude
COVID-affected disbursement windows -- a loan approved in 2017 with a
5-year execution period is still disbursing through 2022). See
Exposure_Classification_Rubric_DRAFT.md's sibling discussion in the
project's chat history for the full reasoning.
"""
import argparse
import csv
import datetime
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

DEFAULT_END_YEAR = datetime.date.today().year

BASE = "https://www.iadb.org"
SEARCH_URL = f"{BASE}/en/project-search"
PROJECT_URL = f"{BASE}/en/project/{{op}}"

HEADERS = {
    "User-Agent": "IDB-research-harvest/1.0 (personal research use; contact: jfg.econ@gmail.com)"
}

# The 26 IDB borrowing member countries (LAC). "Regional" operations are
# excluded by default since they don't map to a single country's exposure.
LAC_COUNTRIES = {
    "Argentina", "Bahamas", "Barbados", "Belize", "Bolivia", "Brazil", "Chile",
    "Colombia", "Costa Rica", "Dominican Republic", "Ecuador", "El Salvador",
    "Guatemala", "Guyana", "Haiti", "Honduras", "Jamaica", "Mexico",
    "Nicaragua", "Panama", "Paraguay", "Peru", "Suriname",
    "Trinidad and Tobago", "Uruguay", "Venezuela",
}

# Statuses that clearly indicate the operation has NOT been approved yet and
# must never be harvested (loan proposals are confidential pre-approval).
# This is a denylist, not an allowlist: anything not matched here is kept but
# flagged for manual review rather than silently included, since only a
# couple of status values were observed directly ("Implementation", "Closed").
PRE_APPROVAL_STATUS_DENYLIST = {"pipeline", "proposed", "under preparation", "in preparation"}

OP_NUMBER_RE = re.compile(r"^[A-Z]{2}-L\d{3,5}$|^[A-Z]{2}\d{4}$")
# Two numbering schemes. The IDB switched around 2002-03: operations approved
# before then carry the old country+serial form (AR0038, BR0216), after that the
# -L form (CO-L1234). The old scheme was missing from this pattern until
# 2026-09-24, which is the sole reason the corpus began in 2003 -- 2,859
# old-format operations sit in the same bulk export and were silently dropped here.
DOC_URL_RE = re.compile(r"https://www\.iadb\.org/document\.cfm\?id=[\w-]+")


def polite_get(url: str, session: requests.Session, delay: float, **kwargs) -> requests.Response:
    """A single, rate-limited, retried GET. Sequential by design -- this is a
    personal research script, not a bulk-crawl operation, and iadb.org gets no
    special treatment just because the documents are public."""
    last_exc = None
    for attempt in range(3):
        try:
            resp = session.get(url, headers=HEADERS, timeout=30, **kwargs)
            time.sleep(delay)
            if resp.status_code == 200:
                return resp
            if resp.status_code == 429:
                time.sleep(delay * 5)
                continue
            resp.raise_for_status()
        except requests.RequestException as e:
            last_exc = e
            time.sleep(delay * 2)
    raise RuntimeError(f"failed to fetch {url}") from last_exc


# --------------------------------------------------------------- Mode A ---
def _pick_column(df, *candidates):
    """The bulk export's column names have shifted between site updates (the
    live search page shows 'Country' / 'Project Status'; the actual
    'Download Project Information' export uses 'Project Country' / 'Status'
    instead) -- try each known alias in order rather than hard-coding one and
    crashing with a bare KeyError when the export doesn't match."""
    for c in candidates:
        if c in df.columns:
            return c
    raise KeyError(f"none of {candidates} found in this file's columns: {list(df.columns)}")


def load_project_list(path: str, start_year: int, end_year: int) -> list:
    """Filter an official bulk export (the 'Download Project Information
    (.xlsx)' button on the search page) down to candidate loan operations.

    Confirmed against a real export (28,656 rows): the Status column only
    ever takes values ACTIVE / EXITED / blank across the *entire* file --
    there is no pipeline/proposed status present anywhere, which suggests
    the export already excludes pre-Board-approval operations. That's
    exactly the confidentiality boundary this pipeline needs, so both
    ACTIVE and EXITED are treated as eligible here; PRE_APPROVAL_STATUS_DENYLIST
    is kept as a defensive check in case a future export does include one."""
    import pandas as pd
    df = pd.read_excel(path) if path.lower().endswith((".xlsx", ".xls")) else pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]

    country_col = _pick_column(df, "Project Country", "Country")
    status_col = _pick_column(df, "Status", "Project Status")

    df["Approval Date"] = pd.to_datetime(df["Approval Date"], errors="coerce")
    keep = (
        df["Project Number"].astype(str).str.match(OP_NUMBER_RE)
        & df[country_col].isin(LAC_COUNTRIES)  # exact match -- multi-country "Regional" rows are excluded on purpose
        & df["Approval Date"].dt.year.between(start_year, end_year)
        & ~df[status_col].astype(str).str.lower().isin(PRE_APPROVAL_STATUS_DENYLIST)
    )

    # Optional, tighter narrowing when these columns are present -- excludes
    # guarantees/grants that share the "-L" numbering pattern, and
    # non-sovereign (private-sector) loans, which aren't the paper's sample.
    # Skipped gracefully (with a note) if the export doesn't have them.
    if "Project Type" in df.columns:
        keep &= df["Project Type"] == "Loan Operation"
    else:
        print("[note] no 'Project Type' column -- skipping the Loan Operation narrowing filter", file=sys.stderr)
    if "Lending Type" in df.columns:
        keep &= df["Lending Type"] == "Sovereign Guaranteed"
    else:
        print("[note] no 'Lending Type' column -- skipping the Sovereign Guaranteed narrowing filter", file=sys.stderr)

    candidates = df.loc[keep, "Project Number"].astype(str).str.strip().unique().tolist()
    return sorted(candidates)


# ------------------------------------------------------- Per-project core ---
def get_project_detail(op_number: str, session: requests.Session, delay: float) -> dict:
    """Fetch one project page and return its metadata plus every document
    listed under the 'Loan Proposal' category. Raw-HTML parsing is
    deliberately avoided here in favor of the same URL + nearby-text pattern
    already confirmed against a real page (see tests/); this keeps the parser
    resilient to markup details this script's author couldn't inspect live."""
    resp = polite_get(PROJECT_URL.format(op=op_number), session, delay)
    return parse_project_page(resp.text, op_number)


def parse_project_page(html: str, op_number: str) -> dict:
    """Converts raw HTML to link-preserving text via html2text, then hands off
    to parse_project_text -- kept as two functions so the text-parsing logic
    (the part that matters) can be unit-tested against a real, captured page
    without needing a live HTML fetch. See tests/test_harvest_parser.py."""
    import html2text
    h = html2text.HTML2Text()
    h.body_width = 0
    h.ignore_images = True
    text = h.handle(html)
    return parse_project_text(text, op_number)


def parse_project_text(text: str, op_number: str) -> dict:
    # html2text (and this fixture, transcribed the same way) puts each field
    # label/value and each document-entry line in its own block, separated by
    # blank lines. Collapsing to non-blank lines turns both the "label, value"
    # pairs and the "url, category, filename, date, language" quintuples into
    # simple fixed-stride windows over one flat list.
    nb = [l.strip() for l in text.splitlines() if l.strip()]

    def field(label: str) -> str:
        for i, l in enumerate(nb):
            if l == label and i + 1 < len(nb):
                return nb[i + 1]
        return ""

    meta = {
        "operation_number": op_number,
        "country": field("Country"),
        "approval_date": field("Approval Date"),
        "project_status": field("Project Status"),
        "lending_instrument": field("Lending Instrument"),
    }

    # Document entries appear as repeating 5-entry blocks: URL, category,
    # filename, date, language. Group non-blank lines into blocks anchored
    # on the URL.
    docs = []
    for i, l in enumerate(nb):
        if DOC_URL_RE.match(l) and i + 4 < len(nb):
            url, category, filename, date, language = nb[i:i + 5]
            if category.lower() == "loan proposal":
                docs.append({"url": url, "filename": filename, "date": date, "language": language})
    meta["loan_proposal_docs"] = docs
    return meta


def is_eligible(meta: dict, start_year: int, end_year: int) -> bool:
    if meta["country"] not in LAC_COUNTRIES:
        return False
    if meta["project_status"].strip().lower() in PRE_APPROVAL_STATUS_DENYLIST:
        return False
    # "Policy-Based Financing" is the label actually used on the live project
    # pages (confirmed against real harvest_log.csv output on 2026-08-14);
    # "Policy-Based Loan" is kept too in case the site's wording varies.
    if meta["lending_instrument"] not in (
        "Investment Loan", "Policy-Based Loan", "Policy-Based Financing"
    ):
        return False
    m = re.search(r"\b(19|20)\d{2}\b", meta["approval_date"])
    if not m or not (start_year <= int(m.group(0)) <= end_year):
        return False
    return True


def already_harvested(op_number: str, corpus_root: Path) -> int:
    """Returns the number of files already sitting in corpus/<op>/, or 0 if
    the folder doesn't exist / is empty. Used to skip a project entirely --
    including its detail-page fetch -- on a re-run, so restarting a
    half-finished harvest doesn't re-hit the network for every project
    already done, only the ones that still need it."""
    op_dir = corpus_root / op_number
    if not op_dir.is_dir():
        return 0
    return sum(1 for f in op_dir.iterdir() if f.is_file() and f.stat().st_size > 0)


def _safe_filename(name: str, limit: int = 150) -> str:
    """Trim a long document title to fit the filesystem WITHOUT losing the
    extension. The IDB names documents with their full title, and the raw name
    was being cut at 150 bytes, which amputated the ".pdf" and made the file
    invisible to the extension filter downstream. Cut the stem instead."""
    stem, dot, ext = name.rpartition(".")
    if not dot or len(ext) > 5 or "/" in ext:
        stem, ext = name, ""
    keep = limit - (len(ext) + 1 if ext else 0)
    b = stem.encode("utf-8")[:max(keep, 1)]
    stem = b.decode("utf-8", "ignore").rstrip()
    return f"{stem}.{ext}" if ext else stem


def download_docs(meta: dict, corpus_root: Path, session: requests.Session, delay: float) -> int:
    op_dir = corpus_root / meta["operation_number"]
    op_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for doc in meta["loan_proposal_docs"]:
        dest = op_dir / _safe_filename(doc["filename"])
        if dest.exists() and dest.stat().st_size > 0:
            continue  # idempotent -- re-running a harvest doesn't re-download
        resp = polite_get(doc["url"], session, delay, stream=True)
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                f.write(chunk)
        n += 1
    return n


# ---------------------------------------------------------------- main ---
def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--project-list", help="official bulk export (.xlsx/.csv) from the search page's "
                                              "'Download Project Information' button -- recommended")
    src.add_argument("--only-operations", help="plain text file, one operation number per line. "
                                               "Harvests exactly these, bypassing the year and "
                                               "lending-instrument filters (the list is trusted).")
    ap.add_argument("--corpus-root", default="../corpus")
    ap.add_argument("--start-year", type=int, default=1998)
    ap.add_argument("--end-year", type=int, default=DEFAULT_END_YEAR)
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between requests")
    ap.add_argument("--limit", type=int, default=None, help="only process the first N candidates (dry runs)")
    ap.add_argument("--log", default="harvest_log.csv")
    ap.add_argument("--force", action="store_true", help="re-check and re-fetch projects even if "
                                                            "corpus/<op>/ already has files")
    args = ap.parse_args()

    corpus_root = Path(args.corpus_root)
    corpus_root.mkdir(parents=True, exist_ok=True)
    session = requests.Session()

    if args.only_operations:
        with open(args.only_operations, encoding="utf-8") as fh:
            candidates = sorted({ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")})
    else:
        candidates = load_project_list(args.project_list, args.start_year, args.end_year)

    if args.limit:
        candidates = candidates[: args.limit]
    print(f"{len(candidates)} candidate operations to check", file=sys.stderr)

    rows = []
    for i, op in enumerate(candidates, 1):
        if not args.force:
            existing = already_harvested(op, corpus_root)
            if existing > 0:
                # Files are already local, so don't re-download -- but still
                # fetch metadata (country/instrument/status/approval_date).
                # An earlier version of this branch skipped that too, which
                # is why, in the August 2026 run, 1,097 of 1,471 rows in harvest_log.csv came back
                # blank on exactly these four fields: any project already on
                # disk from a prior run never got its metadata recorded at
                # all, silently relying on data.xlsx's bulk export to cover
                # the gap. That's a real risk for newly-harvested projects
                # (e.g. the post-2019 extended harvest) that aren't in that
                # snapshot yet.
                try:
                    meta = get_project_detail(op, session, args.delay)
                    rows.append({
                        "operation_number": op, "eligible": "skipped (already downloaded)",
                        "n_docs": existing,
                        "country": meta["country"], "lending_instrument": meta["lending_instrument"],
                        "project_status": meta["project_status"], "approval_date": meta["approval_date"],
                    })
                except Exception as e:
                    print(f"[warn] {op}: already downloaded, but metadata re-fetch failed ({e}) -- "
                          f"row will still be missing country/instrument/status/approval_date this run",
                          file=sys.stderr)
                    rows.append({"operation_number": op, "eligible": "skipped (already downloaded)",
                                 "n_docs": existing, "note": f"metadata fetch failed: {e}"})
                print(f"[{i}/{len(candidates)}] {op}: already have {existing} file(s) in corpus/{op}/ "
                      f"-- skipping download, refreshed metadata (use --force to re-check eligibility/docs)",
                      file=sys.stderr)
                continue

        try:
            meta = get_project_detail(op, session, args.delay)
        except Exception as e:
            print(f"[error] {op}: {e}", file=sys.stderr)
            rows.append({"operation_number": op, "eligible": "error", "n_docs": 0, "note": str(e)})
            continue

        if args.only_operations:
            # The caller supplied the exact list, so the year and instrument
            # screens do not apply. Keep only the pre-approval guard, which is
            # the confidentiality boundary, not a sample filter.
            eligible = meta["project_status"].strip().lower() not in PRE_APPROVAL_STATUS_DENYLIST
        else:
            eligible = is_eligible(meta, args.start_year, args.end_year)
        n = download_docs(meta, corpus_root, session, args.delay) if eligible else 0
        rows.append({
            "operation_number": op, "eligible": eligible, "n_docs": n,
            "country": meta["country"], "lending_instrument": meta["lending_instrument"],
            "project_status": meta["project_status"], "approval_date": meta["approval_date"],
        })
        print(f"[{i}/{len(candidates)}] {op}: eligible={eligible} docs_downloaded={n}", file=sys.stderr)

    with open(args.log, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["operation_number", "eligible", "n_docs", "country",
                                                 "lending_instrument", "project_status", "approval_date", "note"])
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    print(f"Wrote {args.log}", file=sys.stderr)


if __name__ == "__main__":
    main()
