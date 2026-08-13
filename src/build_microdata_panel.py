"""
Stage 3: loop the extraction agent over every project and assemble one
merge-ready microdata table -- the piece that was missing before.

Expected input layout (one subfolder per project, named by operation number,
holding that project's loan proposal + annexes -- exactly what the harvesting
agent described to John would produce from the project-search page). Both
PDF and DOCX annexes are read; other types the harvester may have downloaded
under the same "Loan Proposal" category (e.g. .xlsx procurement plans, .ppt
slide decks) are listed in the run's stderr output but not parsed for text:

    corpus/
      CO-L1234/
        CO-L1234 LP English.pdf
        Anexo tecnico.pdf
        Analisis economico del proyecto.docx
        ...
      BR-L1234/
        ...

Output:
    microdata_panel.csv   -- one row per project, one column per schema field
                              (merge key: operation_number), for the regression.
    microdata_audit.json  -- full record per field: citation + verbatim quote,
                              for spot-checking against the validation sample.

Usage:
    python3 build_microdata_panel.py corpus/ --out microdata_panel.csv
"""
import argparse
import csv
import gc
import json
import os
import sys
from pathlib import Path

from parse_loan_proposal import SUPPORTED_EXTENSIONS, build_project_bundle
from schema import EXTRACTION_TOOL, FIELD_DEFS, build_prompt


def list_project_folders(root: Path):
    return sorted(p for p in root.iterdir() if p.is_dir())


def list_documents(folder: Path):
    """Every PDF/DOCX in the project folder, extension-matched
    case-insensitively (the real IDB site serves some annexes as
    'CO-L1234 IGAS final_rev_07.11.DOCX' -- uppercase extension)."""
    return sorted(str(p) for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS)


def extract_one_project(op_number: str, doc_paths: list, client=None):
    bundle, n_paras, n_relevant, failed_documents = build_project_bundle(doc_paths)
    stats = {"operation_number": op_number, "n_documents": len(doc_paths),
             "n_paragraphs_total": n_paras, "n_paragraphs_relevant": n_relevant,
             "bundle_chars": len(bundle), "n_documents_failed": len(failed_documents),
             "failed_documents": failed_documents}
    # A project where every document failed to parse (e.g. files that were
    # still cloud-sync placeholders) looks identical to one that genuinely
    # has no relevant paragraphs unless this is checked explicitly -- don't
    # silently write a null row for the former.
    if failed_documents and n_paras == 0:
        print(f"[warn] {op_number}: ALL {len(failed_documents)} document(s) failed to parse -- "
              f"this project's row will be null but that's a read failure, not 'nothing found'. "
              f"Re-run after confirming the files are fully downloaded locally.", file=sys.stderr)

    if client is None:
        # Distinct from "bundle was genuinely empty" below -- this is a null
        # row purely because no ANTHROPIC_API_KEY was loaded in this
        # session, not because there was nothing to extract. Flagging it
        # lets the resume logic retry it automatically once a key is
        # present, instead of a dry run's null rows getting permanently
        # mistaken for real "nothing found" results.
        stats["dry_run_no_key"] = True
        return {f[0]: None for f in FIELD_DEFS}, [], stats

    if not bundle:
        return {f[0]: None for f in FIELD_DEFS}, [], stats

    import anthropic  # already imported in main() when a key is present; re-importing
                       # here is free (cached in sys.modules) and lets this function
                       # reference anthropic's exception types directly.

    prompt = build_prompt(bundle)
    try:
        resp = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4000,
            tools=[EXTRACTION_TOOL],
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError:
        # A bad/revoked key fails identically for every remaining project --
        # letting this crash the whole script (or worse, letting it happen
        # silently 1,000+ times while a wrapper script like onedrive_batch.sh
        # keeps pinning/unpinning batches for no benefit) wastes hours. Fail
        # loudly and immediately instead.
        print(f"\n[FATAL] Anthropic API rejected your key (401 invalid x-api-key) while "
              f"processing {op_number}. This will fail identically for every other project, "
              f"so stopping now rather than burning time on the rest of the batch.\n"
              f"Fix: generate a fresh key at console.anthropic.com, put it in .env, then in "
              f"THIS terminal session run:\n  set -a; source .env; set +a\nand confirm with the "
              f"curl test before re-running.\n", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        # Any other API problem (rate limit, timeout, transient 5xx, etc.) --
        # don't crash the whole run over one project. Record it as needing a
        # retry (same idea as failed_documents/dry_run_no_key) and move on.
        print(f"[warn] {op_number}: API call failed ({e}) -- will retry next run", file=sys.stderr)
        stats["api_call_failed"] = str(e)
        return {f[0]: None for f in FIELD_DEFS}, [], stats

    calls = [b.input for b in resp.content if b.type == "tool_use"]
    row = {f[0]: None for f in FIELD_DEFS}
    for c in calls:
        row[c["field"]] = c.get("value")
    return row, calls, stats


def _row_from_calls(op_number: str, calls: list):
    row = {f[0]: None for f in FIELD_DEFS}
    row["operation_number"] = op_number
    for c in calls:
        row[c["field"]] = c.get("value")
    return row


_CURRENT_FIELD_NAMES = {f[0] for f in FIELD_DEFS}


def _entry_is_complete(entry: dict) -> bool:
    """A project only counts as 'done' for resume purposes if it was actually
    read. If every document in it failed to parse (e.g. it was still a
    OneDrive placeholder at the time), n_paragraphs_total is 0 for a read
    -failure reason, not a real 'nothing relevant here' finding -- so it
    should be retried automatically once the files are actually downloaded,
    rather than being permanently skipped just because it was attempted
    once before. Same idea for dry_run_no_key -- a null row written because
    no ANTHROPIC_API_KEY was loaded that session isn't a real result
    either."""
    stats = entry.get("stats", {})
    if stats.get("failed_documents") and stats.get("n_paragraphs_total", 0) == 0:
        return False
    if stats.get("dry_run_no_key"):
        return False
    if stats.get("api_call_failed"):
        return False
    # Catches dry-run rows written BEFORE dry_run_no_key existed as a field
    # (e.g. the very first no-API-key run in this project's history) --
    # extraction always uses tool_choice={"type": "any"}, which forces the
    # model to call the tool at least once on every real request, so a
    # genuinely successful call can never come back with an empty "fields"
    # list. Empty fields despite there being real content sent (bundle_chars
    # > 0) can only mean the API call itself never happened.
    if not entry.get("fields") and stats.get("bundle_chars", 0) > 0:
        return False
    # A genuinely successful past extraction (has real fields recorded) that
    # predates a schema.py field being added is missing that field entirely
    # -- e.g. disbursement_period_years, added 2026-08-13, isn't in any
    # entry extracted before that. Only applies when there ARE recorded
    # fields to begin with: an entry with fields=[] because there were no
    # documents (no_documents_found) or no relevant paragraphs at all
    # (bundle_chars==0) is a legitimate terminal state, not something a
    # schema addition can ever retroactively fill in -- reprocessing it
    # every run would recreate the doc-less-folder infinite-loop bug this
    # function was already fixed for once.
    if entry.get("fields"):
        entry_field_names = {c.get("field") for c in entry["fields"]}
        if not _CURRENT_FIELD_NAMES.issubset(entry_field_names):
            return False
    return True


def _write_outputs(rows: list, audit: dict, out_path: str, audit_out_path: str):
    """Overwrites both output files from the current in-memory state. Cheap
    enough to call after every single project (files are at most a few
    hundred KB for ~1,000 projects) -- and doing so means a run that gets
    killed partway (OOM, closed laptop lid, crash) doesn't lose every
    already-completed -- and already API-billed -- project along with it."""
    fieldnames = ["operation_number"] + [f[0] for f in FIELD_DEFS]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    with open(audit_out_path, "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus_root", help="folder containing one subfolder per project")
    ap.add_argument("--out", default="microdata_panel.csv")
    ap.add_argument("--audit-out", default="microdata_audit.json")
    ap.add_argument("--limit", type=int, default=None, help="only process the first N projects (dry runs)")
    ap.add_argument("--force", action="store_true",
                     help="re-process projects that already have an audit entry from a previous "
                          "(interrupted or completed) run, instead of skipping them")
    ap.add_argument("--only-file", default=None,
                     help="path to a text file of operation numbers (one per line, e.g. from "
                          "plan_batch.py --write-batch) -- restricts this run to just those "
                          "project folders instead of scanning the whole corpus_root. Useful "
                          "when disk space only allows part of the corpus to be downloaded at "
                          "once: without this, every not-yet-downloaded project would still be "
                          "attempted (and retried with delays) on every run.")
    args = ap.parse_args()

    root = Path(args.corpus_root)
    project_folders = list_project_folders(root)
    if args.only_file:
        wanted = {line.strip() for line in Path(args.only_file).read_text(encoding="utf-8").splitlines()
                  if line.strip()}
        project_folders = [p for p in project_folders if p.name in wanted]
        missing = wanted - {p.name for p in project_folders}
        if missing:
            print(f"[warn] {len(missing)} name(s) from {args.only_file} have no matching folder "
                  f"in {args.corpus_root}: {', '.join(sorted(missing)[:10])}"
                  f"{' ...' if len(missing) > 10 else ''}", file=sys.stderr)
    if args.limit:
        project_folders = project_folders[: args.limit]

    # Resume support: if a previous run was interrupted (killed, crashed,
    # laptop went to sleep), don't re-process -- and re-bill -- projects that
    # already completed. Every project already in the audit file is skipped
    # unless --force is passed.
    #
    # --force only forces projects that are actually in scope for THIS run
    # (project_folders, after --only-file/--limit filtering) -- the audit
    # file is still loaded and everything outside that scope is preserved
    # untouched. This matters for the disk-constrained batch workflow: a
    # `--force --only-file batchN.txt` run backfilling a new schema.py field
    # one batch at a time must not silently drop the OTHER ~1,170 projects'
    # already-completed audit entries just because they weren't in this
    # particular batch. (An earlier version of this reset the whole audit
    # dict to empty whenever --force was passed, which would have done
    # exactly that -- caught before it ran against the real corpus.)
    project_folder_names = {p.name for p in project_folders}
    rows, audit = [], {}
    already_done = set()
    if Path(args.audit_out).exists():
        with open(args.audit_out, encoding="utf-8") as f:
            audit = json.load(f)
        n_retry_failed_read, n_retry_no_key, n_force_retry = 0, 0, 0
        for op_number, entry in audit.items():
            if args.force and op_number in project_folder_names:
                # Will be re-processed below regardless of prior completeness
                # -- don't add a row for it yet, or it'd end up duplicated
                # once the retry appends its own.
                n_force_retry += 1
                continue
            if _entry_is_complete(entry):
                already_done.add(op_number)
                rows.append(_row_from_calls(op_number, entry.get("fields", [])))
            else:
                # Will be re-processed below -- don't add a row for it yet,
                # or it'd end up duplicated once the retry appends its own.
                if entry.get("stats", {}).get("dry_run_no_key"):
                    n_retry_no_key += 1
                else:
                    n_retry_failed_read += 1
        if already_done:
            print(f"[resume] found {len(already_done)} already-completed project(s) in "
                  f"{args.audit_out} -- skipping those, continuing with the rest. Use --force "
                  f"to redo everything in scope for this run.", file=sys.stderr)
        if n_force_retry:
            print(f"[force] {n_force_retry} project(s) in this run's scope will be fully "
                  f"reprocessed despite already having a completed audit entry (--force). "
                  f"Any other projects outside this run's scope (e.g. not in --only-file) are "
                  f"left untouched.", file=sys.stderr)
        if n_retry_failed_read:
            print(f"[resume] {n_retry_failed_read} project(s) in {args.audit_out} had every "
                  f"document fail to read last time (likely undownloaded files) -- retrying now "
                  f"that they may be available.", file=sys.stderr)
        if n_retry_no_key:
            print(f"[resume] {n_retry_no_key} project(s) in {args.audit_out} were only recorded "
                  f"as a dry run last time (no ANTHROPIC_API_KEY loaded that session) -- "
                  f"retrying now for real.", file=sys.stderr)
            if not os.environ.get("ANTHROPIC_API_KEY"):
                print(f"[warn] ANTHROPIC_API_KEY still isn't set in THIS session either -- these "
                      f"will just become dry runs again. Run 'export $(cat .env | xargs)' first.",
                      file=sys.stderr)

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    client = None
    if has_key:
        import anthropic
        client = anthropic.Anthropic()
    else:
        print("[warn] No ANTHROPIC_API_KEY set -- running Stage 1 only for every project "
              "(retrieval bundles built, no live extraction). Rows will be written with null "
              "values so you can confirm the panel structure before wiring in credentials.",
              file=sys.stderr)

    n_fully_failed = 0
    n_processed_this_run = 0
    for folder in project_folders:
        op_number = folder.name
        if op_number in already_done:
            continue
        docs = list_documents(folder)
        if not docs:
            # Record this permanently (empty failed_documents = "complete" to
            # _entry_is_complete/load_completed) rather than just printing and
            # moving on -- otherwise a folder with no PDF/DOCX ever downloaded
            # under it (e.g. the harvester found nothing eligible) can NEVER
            # be marked done, and plan_batch.py/run_all_batches.sh will offer
            # it up as "pending" every single round forever, looping
            # indefinitely on a project that was never going to complete.
            print(f"[skip] {op_number}: no PDF/DOCX files found -- recording as resolved "
                  f"(nothing to extract), not retrying", file=sys.stderr)
            stats = {"operation_number": op_number, "n_documents": 0, "n_paragraphs_total": 0,
                      "n_paragraphs_relevant": 0, "bundle_chars": 0, "n_documents_failed": 0,
                      "failed_documents": [], "no_documents_found": True}
            row = {f[0]: None for f in FIELD_DEFS}
            row["operation_number"] = op_number
            rows.append(row)
            audit[op_number] = {"stats": stats, "fields": []}
            _write_outputs(rows, audit, args.out, args.audit_out)
            continue
        row, calls, stats = extract_one_project(op_number, docs, client)
        row["operation_number"] = op_number
        rows.append(row)
        audit[op_number] = {"stats": stats, "fields": calls}
        n_processed_this_run += 1
        if stats["failed_documents"] and stats["n_paragraphs_total"] == 0:
            n_fully_failed += 1
        print(f"[ok] {op_number}: {stats['n_paragraphs_relevant']}/{stats['n_paragraphs_total']} "
              f"paragraphs relevant across {stats['n_documents']} doc(s)", file=sys.stderr)

        # Checkpoint after every project, not just at the end -- see
        # _write_outputs' docstring for why this matters for a ~1,000+
        # project run that can take hours.
        _write_outputs(rows, audit, args.out, args.audit_out)

        # PDF parsing libraries (pdfplumber/pdfminer) are known to leave
        # circular references (font/layout caches) that Python's normal
        # refcounting won't free promptly. Across hundreds of projects in
        # one long-running process, that accumulates -- a likely contributor
        # to this script getting OOM-killed (exit code 137 / "zsh: killed")
        # partway through a large batch. Forcing a collection after every
        # project is cheap insurance against that.
        gc.collect()

    if n_fully_failed:
        print(f"\n[SUMMARY] {n_fully_failed}/{len(rows)} project(s) had EVERY document fail to "
              f"parse (read errors, not 'no relevant content') -- their rows are null but "
              f"shouldn't be trusted as a real finding. Check microdata_audit.json for "
              f"'failed_documents' entries, make sure the corpus is fully downloaded locally "
              f"(not cloud-only placeholders), and re-run.\n", file=sys.stderr)

    print(f"Wrote {args.out} ({len(rows)} projects total, {n_processed_this_run} processed this run)")
    print(f"Wrote {args.audit_out} (citations + quotes per field, for spot-checking)")


if __name__ == "__main__":
    main()
