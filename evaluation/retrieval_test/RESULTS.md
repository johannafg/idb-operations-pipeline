# Retrieval fix: before and after

Stage 1 is deterministic, so the retrieval changes can be measured without any
model calls. `compare_retrieval.py` runs the previous and revised versions of
`src/parse_loan_proposal.py` on the same 40 operations and records what each
would send to the model. `_old_parse.py` is the previous version, kept for this
comparison.

The sample (`sample.txt`, fixed seed) contains the six operations discussed in
`../error_analysis.md`, eight operations that previously retrieved no relevant
paragraphs, six from English-language borrowers, and twenty from Spanish- and
Portuguese-language borrowers. Raw results are in `results.jsonl`.

## Changes tested

Spanish and Portuguese terms added to all six keyword groups. New groups for
imported inputs, price adjustment, and civil works. Accent-insensitive matching.
When a document's relevant text exceeds the character budget, paragraphs are now
kept by relevance score, with the sparse import and price-adjustment groups
weighted up, instead of by position in the document.

## Results

| | before | after |
|---|---:|---:|
| paragraphs tagged relevant, share of parsed | 5.1% | 19.0% |
| Spanish/Portuguese borrowers | 5.1% | 22.4% |
| English-language borrowers | 5.2% | 10.8% |
| operations with no relevant paragraph | 9 of 40 | 1 of 40 |
| price-adjustment passages reaching the model | 1 of 46 (2%) | 21 of 46 (46%) |
| imported-input passages reaching the model | 3 of 125 (2%) | 69 of 125 (55%) |

The reference passages are found by a broad pattern over the full text of each
document. They are an upper bound on relevant text, not ground truth. Many
"imports" matches describe national trade, not project inputs.

AR-L1436, whose paragraph 3.1 commits the works contracts to price
redetermination formulas, went from 0 relevant paragraphs to 40, and paragraph
3.1 now reaches the model.

## Costs and remaining limits

The excerpt sent to the model roughly triples in length, from about 7,200 to
about 23,700 characters per operation before the overall cap. Extraction cost
per operation rises in proportion.

About half of the reference passages still fall outside the excerpt, because the
per-document budget binds. The overall 24,000-character cap in
`build_project_bundle` still truncates by position across documents. Documents
with no text layer still yield nothing and need optical character recognition.

Whether more retrieved text produces more correct extractions, and not just more
extractions, is tested separately on a hand-checked subsample.
