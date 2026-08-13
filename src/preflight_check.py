"""
Run this BEFORE build_microdata_panel.py on a real, full-size corpus.

What happened last run: 1,123 rows came back with every field empty. Cause:
most corpus files were still OneDrive "on-demand" placeholders (visible in
Finder/`ls`, not actually downloaded) when the pipeline tried to read them.
The old code treated every one of those read failures as "0 relevant
paragraphs found" -- so the run finished without crashing and produced a
structurally valid but completely empty CSV.

This script does nothing but check: it walks every project folder, tries to
read the first byte of every PDF/DOCX with a few retries, and reports which
files are NOT actually downloaded yet. It makes no API calls, so it's free
and fast to run before -- and after -- kicking off the real (expensive)
extraction run.

Usage:
    python3 preflight_check.py corpus/
    python3 preflight_check.py corpus/ --only-file batch1.txt
        # restrict the check to just the project folders listed in
        # batch1.txt (one operation number per line, e.g. from
        # plan_batch.py --write-batch) -- for checking whether one batch
        # has finished downloading, rather than the entire corpus (which,
        # in a batched/limited-disk-space workflow, is mostly *supposed*
        # to be undownloaded at any given moment -- checking the whole
        # thing would report "stuck" forever).
"""
import sys
import time
from pathlib import Path

SUPPORTED_EXTENSIONS = {".pdf", ".docx"}


def try_read(path: Path, attempts: int = 3, base_delay: float = 1.0):
    last_exc = None
    for attempt in range(attempts):
        try:
            with open(path, "rb") as f:
                f.read(1)
            return True, None
        except OSError as e:
            last_exc = e
            time.sleep(base_delay * (attempt + 1))
    return False, str(last_exc)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 preflight_check.py corpus/ [--only-file batch.txt]")
        sys.exit(1)

    root = Path(sys.argv[1])
    only_file = None
    if "--only-file" in sys.argv:
        only_file = sys.argv[sys.argv.index("--only-file") + 1]

    project_folders = sorted(p for p in root.iterdir() if p.is_dir())
    if only_file:
        wanted = {line.strip() for line in Path(only_file).read_text(encoding="utf-8").splitlines()
                  if line.strip()}
        project_folders = [p for p in project_folders if p.name in wanted]

    stuck = []
    total_files = 0
    for folder in project_folders:
        docs = sorted(p for p in folder.iterdir()
                       if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS)
        for doc in docs:
            total_files += 1
            ok, err = try_read(doc)
            if not ok:
                stuck.append((folder.name, doc.name, err))
            print(f"\r  checked {total_files} files, {len(stuck)} stuck so far...", end="", file=sys.stderr)

    print()  # newline after the progress line
    print(f"\nChecked {total_files} files across {len(project_folders)} project folders.")

    if not stuck:
        print("All files are locally readable. Safe to run build_microdata_panel.py now.")
        return

    print(f"\n{len(stuck)} file(s) are NOT downloaded locally yet (still cloud placeholders):")
    for op_number, filename, err in stuck[:20]:
        print(f"  - {op_number}/{filename}  ({err})")
    if len(stuck) > 20:
        print(f"  ... and {len(stuck) - 20} more")

    affected_projects = sorted({op for op, _, _ in stuck})
    print(f"\n{len(affected_projects)} project(s) affected. If you run the extraction now, "
          f"these projects' rows will come back empty again.")
    print("\nTo fix: in Finder, select the corpus/ folder (or the whole IDB_Loans folder) -> "
          "right-click -> 'Always Keep on This Device'. Wait for the cloud icons next to the "
          "files to turn into plain checkmarks (fully downloaded), then re-run this check.")
    sys.exit(1)


if __name__ == "__main__":
    main()
