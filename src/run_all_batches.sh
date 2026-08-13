#!/bin/bash
# Fully automates the disk-constrained extraction loop: repeatedly plans a
# batch that fits your free space, downloads it, runs extraction, frees the
# space, and moves to the next batch -- until the whole corpus is done.
# You run this one command and walk away; no per-round manual steps.
#
# Run this in your Mac's own Terminal (it needs the real OneDrive app).
#
# Usage:
#   chmod +x run_all_batches.sh   # only needed the first time
#   ./run_all_batches.sh <free_gb> <corpus_root> [panel_out.csv] [audit_out.json]
#
# Example:
#   ./run_all_batches.sh 1 ../corpus ../output/microdata_panel_latest.csv
#
# Set FORCE=1 to reprocess the ENTIRE corpus from scratch instead of just
# what's still pending -- e.g. after adding a new field to schema.py that
# needs backfilling for already-completed projects too. This redownloads
# and re-extracts every project (real API cost for all of them, not just
# the new field), so only use it when you actually need every field
# refreshed, not just the new one.
#   FORCE=1 ./run_all_batches.sh 1 ../corpus ../output/microdata_panel_latest.csv

set -euo pipefail

FREE_GB="${1:?Usage: $0 free_gb corpus_root [panel_out.csv] [audit_out.json]}"
CORPUS_ROOT="${2:?missing corpus_root}"
PANEL_OUT="${3:-../output/microdata_panel_latest.csv}"
AUDIT_OUT="${4:-microdata_audit.json}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BATCH_FILE="$SCRIPT_DIR/.current_batch.txt"

chmod +x "$SCRIPT_DIR/onedrive_batch.sh"

round=1
while true; do
    echo ""
    echo "=================== Round $round ==================="

    PLAN_FORCE_FLAG=()
    if [ "${FORCE:-0}" = "1" ]; then
        PLAN_FORCE_FLAG=(--force)
    fi

    set +e
    python3 "$SCRIPT_DIR/plan_batch.py" "$CORPUS_ROOT" --free-gb "$FREE_GB" \
        --audit-out "$AUDIT_OUT" --write-batch "$BATCH_FILE" "${PLAN_FORCE_FLAG[@]}"
    code=$?
    set -e

    if [ "$code" -eq 2 ]; then
        echo ""
        echo "All projects completed -- nothing left to process."
        break
    elif [ "$code" -eq 1 ]; then
        echo ""
        echo "Stopped: not enough free space for even one more project folder." >&2
        echo "Free up more space (Trash, old Downloads, About This Mac > Storage > Manage)" >&2
        echo "and re-run this same command -- it'll pick up exactly where it stopped." >&2
        exit 1
    fi

    "$SCRIPT_DIR/onedrive_batch.sh" run "$BATCH_FILE" "$CORPUS_ROOT" "$PANEL_OUT" "$AUDIT_OUT"
    round=$((round + 1))
done

echo ""
echo "All batches processed. Next: python3 build_excel_report.py --panel $PANEL_OUT --audit $AUDIT_OUT --out ${PANEL_OUT%.csv}.xlsx"
