"""
Figures out which project folders to download next when your disk doesn't
have room for the whole corpus at once (see preflight_check.py for checking
what's already downloaded, and the Setup Guide's "limited disk space"
section for the full workflow this plugs into).

File sizes are readable from OneDrive placeholders without downloading them
(that's just filesystem metadata), so this needs no download and is instant
even on the full corpus.

Usage:
    python3 plan_batch.py ../corpus --free-gb 1
    python3 plan_batch.py ../corpus --free-gb 5 --audit-out microdata_audit.json

If --audit-out points at an existing audit file, projects that already
completed successfully are left out of the plan (no need to re-download
them). A --margin knob (default 0.7) keeps some of your stated free space as
a safety buffer rather than planning right up to the edge.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build_microdata_panel import _entry_is_complete  # single source of truth --
# this duplicated its own copy of the completeness rules once already, which is
# exactly how the dry-run-retry logic silently drifted out of sync between the
# two files before.

SUPPORTED_EXTENSIONS = {".pdf", ".docx"}


def folder_size_bytes(folder: Path) -> int:
    total = 0
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
            try:
                total += p.stat().st_size
            except OSError:
                pass  # metadata unreadable for some reason -- skip rather than crash
    return total


def load_completed(audit_path: str, force: bool = False):
    if force:
        # e.g. a schema.py field was added and the whole corpus needs
        # reprocessing to backfill it -- plan every project as pending,
        # in disk-sized batches, same as a from-scratch run.
        return set()
    if not audit_path or not Path(audit_path).exists():
        return set()
    with open(audit_path, encoding="utf-8") as f:
        audit = json.load(f)
    done = set()
    for op_number, entry in audit.items():
        if _entry_is_complete(entry):
            done.add(op_number)
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus_root")
    ap.add_argument("--free-gb", type=float, required=True,
                     help="how much free disk space you currently have, in GB")
    ap.add_argument("--margin", type=float, default=0.7,
                     help="fraction of --free-gb to actually plan for (default 0.7, i.e. "
                          "leave 30%% headroom for the OS and everything else)")
    ap.add_argument("--audit-out", default="microdata_audit.json",
                     help="skip projects already completed in this audit file, if it exists")
    ap.add_argument("--write-batch", default=None,
                     help="also save the batch's folder names to this file, one per line, so "
                          "build_microdata_panel.py --only-file can process just this batch "
                          "instead of scanning the whole (mostly not-yet-downloaded) corpus")
    ap.add_argument("--force", action="store_true",
                     help="plan every project as pending, ignoring the audit file entirely -- "
                          "use when a schema.py field was added and the whole corpus needs "
                          "reprocessing to backfill it. Pass --force through to "
                          "build_microdata_panel.py as well, or this will just redownload "
                          "everything without actually re-extracting it.")
    args = ap.parse_args()

    root = Path(args.corpus_root)
    budget_bytes = args.free_gb * args.margin * (1024 ** 3)
    completed = load_completed(args.audit_out, force=args.force)

    project_folders = sorted(p for p in root.iterdir() if p.is_dir())
    pending = [p for p in project_folders if p.name not in completed]

    if not pending:
        print("Nothing pending -- every project is already completed in the audit file.")
        sys.exit(2)  # distinct from 0 (batch written) and 1 (no space) -- lets a wrapper
                      # script (e.g. run_all_batches.sh) tell "fully done" apart from
                      # "wrote a batch, keep going" without parsing stdout

    batch, used = [], 0
    total_pending_bytes = 0
    for folder in pending:
        size = folder_size_bytes(folder)
        total_pending_bytes += size
        if used + size <= budget_bytes:
            batch.append(folder.name)
            used += size

    print(f"{len(completed)} project(s) already done, {len(pending)} pending "
          f"({total_pending_bytes / (1024**3):.1f} GB total remaining).")
    print(f"Budget: {args.free_gb} GB free x {args.margin} margin = "
          f"{budget_bytes / (1024**3):.2f} GB to work with this round.\n")

    if not batch:
        print("Not enough free space for even one more project folder -- free up more "
              "space (Trash, old Downloads, About This Mac > Storage > Manage) before "
              "continuing.")
        sys.exit(1)

    print(f"This round: select these {len(batch)} folder(s) in Finder and set "
          f"'Always Keep on This Device' ({used / (1024**3):.2f} GB):\n")
    for name in batch:
        print(f"  {name}")

    if args.write_batch:
        Path(args.write_batch).write_text("\n".join(batch) + "\n", encoding="utf-8")
        force_flag = " --force" if args.force else ""
        print(f"\nSaved this list to {args.write_batch} -- once these are downloaded, run:\n"
              f"  python3 build_microdata_panel.py {args.corpus_root} "
              f"--out ../output/microdata_panel_latest.csv --only-file {args.write_batch}{force_flag}")

    remaining = len(pending) - len(batch)
    if remaining:
        print(f"\n{remaining} project(s) left for later rounds at this pace -- run "
              f"build_microdata_panel.py on this batch, free the space back up in Finder "
              f"('Free Up Space' on those same folders), then re-run this script for the "
              f"next batch.")
    else:
        print("\nThis is the last batch -- everything else already fits.")


if __name__ == "__main__":
    main()
