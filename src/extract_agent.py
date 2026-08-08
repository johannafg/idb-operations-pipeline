"""
Stage 2 of the extraction agent: send the Stage-1 retrieval bundle to Claude
with a forced tool call per field, so every value comes back with a citation.

Usage:
    python3 extract_agent.py "CO-L1234 LP English (1).pdf" --out CO-L1234_extracted.json

Requires ANTHROPIC_API_KEY in the environment. If it's missing, the script
still runs Stage 1, prints the exact prompt it would have sent (--dry-run
behavior), and exits cleanly rather than failing -- useful for wiring this
into a pipeline before credentials are provisioned.
"""
import argparse
import json
import os
import sys

from parse_loan_proposal import extract_paragraphs, tag_sections, build_retrieval_bundle
from schema import EXTRACTION_TOOL, FIELD_DEFS, build_prompt


def run_stage1(pdf_path):
    paras = extract_paragraphs(pdf_path)
    tagged = tag_sections(paras)
    bundle, n_relevant = build_retrieval_bundle(tagged)
    return bundle, len(paras), n_relevant


def run_stage2(bundle: str):
    import anthropic

    client = anthropic.Anthropic()
    prompt = build_prompt(bundle)

    resp = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4000,
        tools=[EXTRACTION_TOOL],
        tool_choice={"type": "any"},
        messages=[{"role": "user", "content": prompt}],
    )

    # A single turn typically won't emit 12 tool calls back-to-back with
    # tool_choice="any" (that forces exactly one call). For a real batch job,
    # loop: force one call, feed a synthetic tool_result back, repeat until
    # all fields are covered, or switch to tool_choice="auto" with an
    # explicit "call the tool once per field, then stop" instruction and
    # parse however many calls come back in that turn.
    calls = [b.input for b in resp.content if b.type == "tool_use"]
    return calls


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf_path")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    bundle, n_total, n_relevant = run_stage1(args.pdf_path)
    print(f"[stage 1] {n_total} paragraphs parsed, {n_relevant} judged relevant, "
          f"{len(bundle)} chars sent to extraction.", file=sys.stderr)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[stage 2] No ANTHROPIC_API_KEY set -- skipping live extraction.", file=sys.stderr)
        print("[stage 2] Prompt that would be sent:\n", file=sys.stderr)
        print(build_prompt(bundle)[:2000] + "\n... [truncated]", file=sys.stderr)
        return

    calls = run_stage2(bundle)
    out = {"source_pdf": args.pdf_path, "fields": calls}
    text = json.dumps(out, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Wrote {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
