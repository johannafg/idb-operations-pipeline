#!/bin/bash
# Automates the "download a batch, process it, free the space" cycle using
# OneDrive's own scriptable pin/unpin commands -- no manual Finder clicking,
# one folder at a time or otherwise. Source:
# https://learn.microsoft.com/en-us/sharepoint/files-on-demand-mac
#
# Run this on your Mac's own Terminal (not inside any sandbox) -- it needs
# to quit and relaunch the real OneDrive app.
#
# Usage:
#   ./onedrive_batch.sh pin    batch1.txt ../corpus          # download this batch
#   ./onedrive_batch.sh unpin  batch1.txt ../corpus          # free this batch's space
#   ./onedrive_batch.sh run    batch1.txt ../corpus ../output/microdata_panel_latest.csv [audit_out.json]
#       # full cycle: pin -> wait until downloaded -> run extraction -> unpin
#
# batch1.txt is the file plan_batch.py writes with --write-batch (one
# operation number per line, matching a folder name under corpus_root).
#
# IMPORTANT: always pass the same panel_out / audit_out paths you use with
# build_microdata_panel.py directly. If audit_out is left out, it defaults
# to microdata_audit.json in THIS script's folder -- same as
# build_microdata_panel.py's own default -- so it still lines up correctly
# as long as you're consistent, but an explicit path avoids any ambiguity.
#
# Set FORCE=1 in the environment (e.g. `FORCE=1 ./onedrive_batch.sh run ...`)
# to reprocess every project in the batch even if it already has a
# completed audit entry -- e.g. after adding a new field to schema.py that
# needs backfilling across the whole corpus. Make sure BATCH_FILE itself
# was planned with `plan_batch.py --force` too, or it will only contain
# already-completed projects (nothing left pending to plan).

set -euo pipefail

ACTION="${1:?Usage: $0 [pin|unpin|run] batch_file.txt corpus_root [panel_out.csv] [audit_out.json]}"
BATCH_FILE="${2:?missing batch file}"
CORPUS_ROOT_ARG="${3:?missing corpus_root}"
PANEL_OUT="${4:-../output/microdata_panel_latest.csv}"
AUDIT_OUT="${5:-microdata_audit.json}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The OneDrive helper talks to the running OneDrive app over IPC rather than
# operating on the filesystem directly -- it has no notion of this shell's
# working directory, so a relative path like "../corpus/CH-L1015" can't be
# resolved and every /pin or /unpin call fails (status=-3). Resolve to an
# absolute path up front so this can't happen.
CORPUS_ROOT="$(cd "$CORPUS_ROOT_ARG" && pwd)"

find_onedrive_bin() {
    local candidates=(
        "/Applications/OneDrive.app/Contents/MacOS/OneDrive"
        "/Applications/OneDrive.App/Contents/MacOS/OneDrive"
    )
    for c in "${candidates[@]}"; do
        if [ -f "$c" ]; then echo "$c"; return 0; fi
    done
    local found
    found=$(mdfind "kMDItemFSName == 'OneDrive.app'" 2>/dev/null | head -1)
    if [ -n "$found" ] && [ -f "$found/Contents/MacOS/OneDrive" ]; then
        echo "$found/Contents/MacOS/OneDrive"
        return 0
    fi
    echo "" # not found
}

ONEDRIVE_BIN="$(find_onedrive_bin)"
if [ -z "$ONEDRIVE_BIN" ]; then
    echo "Could not find the OneDrive app under /Applications. Edit find_onedrive_bin() in this script with the correct path, or fall back to doing this batch's Finder step manually." >&2
    exit 1
fi

quit_onedrive() {
    # AppleScript's `quit` alone isn't reliable here -- OneDrive runs as a
    # background/menu-bar app and can ignore the standard quit event
    # entirely, leaving it running (confirmed via pgrep). The pin/unpin
    # commands fail with exit code 1 whenever that happens. Escalate to
    # killall, then killall -9, verifying with pgrep at each step instead of
    # just hoping a fixed sleep was long enough.
    osascript -e 'quit app "OneDrive"' 2>/dev/null || true
    for _ in 1 2 3 4 5; do
        pgrep -x OneDrive >/dev/null 2>&1 || return 0
        sleep 1
    done

    killall OneDrive 2>/dev/null || true
    for _ in 1 2 3 4 5; do
        pgrep -x OneDrive >/dev/null 2>&1 || return 0
        sleep 1
    done

    killall -9 OneDrive 2>/dev/null || true
    sleep 2
    if pgrep -x OneDrive >/dev/null 2>&1; then
        echo "[warn] OneDrive is still running after quit/killall/killall -9 -- pin/unpin calls " >&2
        echo "  will likely fail with exit code 1. You may need to quit it manually from the " >&2
        echo "  menu bar icon before this can proceed." >&2
    fi
}

relaunch_onedrive() {
    open -a OneDrive
    sleep 3
}

get_pin_state() {
    # This CLI's exit code isn't trustworthy for success/failure (confirmed:
    # /getpin printed a correct "pin state=Pinned" result while still
    # exiting 1) -- so verify the ACTUAL state afterward instead of trusting
    # exit codes anywhere in this script. The `|| true` on both lines is
    # deliberate and load-bearing: with `set -o pipefail` active, the
    # OneDrive binary's own always-1 exit code would otherwise propagate
    # through the pipe and trip `set -e` at the call site
    # (`x="$(get_pin_state ...)"` is a simple command, not exempt from -e),
    # killing the whole script after the very first folder -- which is
    # exactly what happened before this fix.
    local out
    out="$("$ONEDRIVE_BIN" /getpin "$1" 2>&1 || true)"
    printf '%s' "$out" | grep -o 'pin state=[A-Za-z]*' | sed 's/pin state=//' || true
}

set_pin_state() {
    local mode="$1"  # /pin or /unpin
    local want_state
    [ "$mode" = "/pin" ] && want_state="Pinned" || want_state="not-Pinned"
    local n_ok=0 n_failed=0
    quit_onedrive
    while IFS= read -r op_number; do
        [ -z "$op_number" ] && continue
        local path="$CORPUS_ROOT/$op_number"
        if [ ! -d "$path" ]; then
            echo "[warn] no such folder: $path -- skipping" >&2
            continue
        fi
        echo "  $mode $path"
        "$ONEDRIVE_BIN" "$mode" "$path" /r >/dev/null 2>&1 || true  # exit code ignored -- see get_pin_state
        local actual_state
        actual_state="$(get_pin_state "$path")"
        if { [ "$want_state" = "Pinned" ] && [ "$actual_state" = "Pinned" ]; } || \
           { [ "$want_state" = "not-Pinned" ] && [ "$actual_state" != "Pinned" ]; }; then
            n_ok=$((n_ok + 1))
        else
            echo "  [warn] $mode didn't take for $path (state is now '$actual_state') -- continuing with the rest of the batch" >&2
            n_failed=$((n_failed + 1))
        fi
    done < "$BATCH_FILE"
    echo "$mode: $n_ok succeeded, $n_failed failed (verified by actual state, not exit code)."
    LAST_PIN_OK=$n_ok
    LAST_PIN_FAILED=$n_failed
    relaunch_onedrive
}

wait_until_downloaded() {
    local max_wait_s=1800   # 30 minutes; adjust if your connection is slow
    local interval_s=15
    local elapsed=0
    echo "Waiting for OneDrive to finish downloading this batch..."
    while [ "$elapsed" -lt "$max_wait_s" ]; do
        # Only checks THIS batch's files, not the whole corpus -- most of the
        # corpus is expected to be undownloaded at any given time in this
        # workflow, so checking the whole thing would never report "ready".
        if python3 "$SCRIPT_DIR/preflight_check.py" "$CORPUS_ROOT" --only-file "$BATCH_FILE" >/tmp/preflight_out.txt 2>&1; then
            echo "All files in the batch are downloaded."
            return 0
        fi
        sleep "$interval_s"
        elapsed=$((elapsed + interval_s))
        echo "  ...still downloading (${elapsed}s elapsed)"
    done
    echo "[warn] gave up waiting after ${max_wait_s}s -- check 'python3 preflight_check.py $CORPUS_ROOT --only-file $BATCH_FILE' manually, some files may still be downloading (especially on a slow connection)." >&2
    return 1
}

case "$ACTION" in
    pin)
        echo "Pinning (downloading) batch from $BATCH_FILE ..."
        set_pin_state /pin
        ;;
    unpin)
        echo "Unpinning (freeing space for) batch from $BATCH_FILE ..."
        set_pin_state /unpin
        ;;
    run)
        echo "=== 1/4: pinning batch ==="
        set_pin_state /pin
        if [ "$LAST_PIN_OK" -eq 0 ] && [ "$LAST_PIN_FAILED" -gt 0 ]; then
            echo "[warn] every /pin call failed for this batch -- skipping the wait (it would " >&2
            echo "  just time out) and trying extraction anyway in case some files were already " >&2
            echo "  downloaded from before. If this keeps happening, see the Setup Guide's " >&2
            echo "  troubleshooting section for the Finder fallback." >&2
        else
            echo "=== 2/4: waiting for download ==="
            wait_until_downloaded || true
        fi
        echo "=== 3/4: running extraction on this batch ==="
        extraction_rc=0
        FORCE_FLAG=()
        if [ "${FORCE:-0}" = "1" ]; then
            echo "FORCE=1 -- reprocessing every project in this batch, not just incomplete ones."
            FORCE_FLAG=(--force)
        fi
        python3 "$SCRIPT_DIR/build_microdata_panel.py" "$CORPUS_ROOT" --out "$PANEL_OUT" \
            --audit-out "$AUDIT_OUT" --only-file "$BATCH_FILE" "${FORCE_FLAG[@]}" || extraction_rc=$?
        echo "=== 4/4: unpinning batch (freeing space) ==="
        # Always free this batch's space, even if extraction crashed above --
        # otherwise a bad API key (or any other crash) leaves the batch's
        # files stuck taking up disk on top of whatever already went wrong.
        set_pin_state /unpin
        if [ "$extraction_rc" -ne 0 ]; then
            echo "[FATAL] extraction failed (exit code $extraction_rc) -- this batch's disk space " >&2
            echo "  was freed, but stopping here rather than continuing to the next batch. See " >&2
            echo "  the error above for what went wrong." >&2
            exit "$extraction_rc"
        fi
        echo "Done with this batch. Run plan_batch.py again for the next one."
        ;;
    *)
        echo "Unknown action '$ACTION' -- expected pin, unpin, or run." >&2
        exit 1
        ;;
esac
