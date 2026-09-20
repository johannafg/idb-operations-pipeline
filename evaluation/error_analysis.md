# Error analysis

The pipeline's lowest-coverage fields do not fail at extraction. They fail at
retrieval, before the model sees the relevant text. The evidence comes from the
status flag recorded in the audit log and from one operation where the correct
answer is known in advance.

All figures below are produced by `evaluation/diagnose_audit.py`. Its output is
saved in `evaluation/diagnose_audit_output.txt`.

## A case with a known answer

AR-L1436, a sustainable road infrastructure program in Entre Ríos, Argentina, was
coded by hand during prototyping. Paragraph 3.1 of its loan proposal contains an
explicit price-redetermination formula governing the works contracts.

In the production run, the parser recovered 67 paragraphs from that document and
the retrieval step tagged none of them as relevant. The model received an empty
excerpt and correctly reported every field as not stated. The panel therefore
records no price-escalation clause for an operation that has one.

The model behaved as designed. The error occurred upstream, and the status flag
is what makes it visible. A pipeline that returned only a value or a null would
have recorded this as a documentary absence and nobody would have known.

## Four mechanisms

### Retrieval does not look for three of the fields

Retrieval selects paragraphs by matching against six topic groups in
`src/parse_loan_proposal.py`: financing, procurement, risks, execution,
disbursement, and safeguards. None of them targets imported inputs or price
adjustment. A paragraph describing either is retrieved only if it happens to also
match an unrelated keyword.

The status counts show the consequence. `imported_share_est` is never found.
`price_escalation_clause` is found once. `imported_inputs_present` is found seven
times. Across the three fields, more than 99 percent of assigned statuses are
`not_stated`. `construction_share_pct`, found in 5.8 percent of operations, shares
the same exposure.

The single price-escalation hit, SU-L1021, came from an English section heading,
"Qualification and Price Escalation," which was retrieved incidentally.

### Retrieval keywords are English-only

Every keyword in the retrieval lists is English. Most of the sample consists of
Spanish- and Portuguese-language proposals.

Among English-language borrowers, 10.4 percent of parsed paragraphs are retrieved
as relevant. Among Spanish- and Portuguese-language borrowers, the figure is
5.2 percent. The gap in fields found per operation is smaller, 4.93 against 4.42,
because many Spanish-language proposals contain English passages and because the
model extracts well from whatever it does receive.

This comparison is suggestive rather than conclusive. Language is confounded with
country, and the English-speaking borrowers are small Caribbean economies with a
different project mix. A document-level language flag would give the cleaner test.

### Some documents yield no text

Ninety-seven operations, 8.0 percent of the sample, produced zero parsed
paragraphs. The share of operations with no relevant paragraphs is nearly the same
in both language groups, 11.5 and 12.0 percent, which separates this mechanism
from the language gap. The probable cause is scanned proposals with no text layer,
which the PDF parser cannot read. The lowest-yield countries are consistent with
this. Venezuela averages 1.26 fields found per operation, against a sample mean
near 4.5, and its operations are concentrated in older vintages.

### Truncation

Forty-five operations, 3.7 percent, reached the 24,000-character bundle cap.
Retrieval appends paragraphs in document order until the cap binds, so in long
documents the later sections are dropped regardless of their relevance.

A further 22 documents across 13 operations could not be opened at all, mostly
corrupted or mislabeled Word and PDF files. That problem is minor by comparison.

## Precision on the few positives

The low-coverage fields also show a precision problem among the values they do
record. On a first reading of the seven operations where `imported_inputs_present`
was found, four of the supporting quotes do not describe imported project inputs.
One is an exclusion list barring the import of invasive species. One describes a
bidding threshold. One describes a project delay. One describes an outsourced
service arrangement. The remaining three, covering aircraft purchased from a
foreign manufacturer, import costs excluded from a cost estimate, and a liquefied
natural gas import arrangement, appear correct.

That reading needs confirmation by hand-coding. It suggests the model treats any
mention of importing or international procurement as evidence for the field, and
that the field definition in `src/schema.py` should require the quote to describe
goods the project itself will import.

## What this analysis does not establish

Status counts describe coverage. They do not measure the accuracy of the values
the pipeline does report. A `found` value can still be wrong, as the precision
reading above shows. Field-level precision and recall require a hand-coded
validation sample, which is the next piece of work.

## Fix plan

Each change will be validated on a small stratified sample, including AR-L1436,
before the full corpus is re-run.

1. Add retrieval groups for imported inputs and for price adjustment, in English,
   Spanish, and Portuguese. Candidate terms include *importación*, *insumos
   importados*, *importação*, *redeterminación de precios*, *reajuste de precios*,
   *fórmula polinómica*, *reajuste de preços*, *price adjustment*, and
   *price escalation*.
2. Add Spanish and Portuguese equivalents to the six existing groups, for example
   *cofinanciamiento*, *adquisiciones*, *licitación pública internacional*,
   *organismo ejecutor*, *desembolsos*, *salvaguardias*, *aquisições*, and
   *licitação*.
3. Add an optical character recognition fallback for documents that parse to zero
   paragraphs.
4. Replace document-order truncation with relevance-ranked selection, so that when
   the cap binds it drops the least relevant paragraphs rather than the last ones.
5. Tighten the definition of `imported_inputs_present` in `src/schema.py`.
6. Re-run the full corpus with `disbursement_period_years` included.
