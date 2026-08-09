"""
Unit test for harvest_projects.parse_project_text, run against a fixture
built from the REAL text returned by https://www.iadb.org/en/project/CO-L1234
(fetched directly and pasted into tests_fixture_co_l1234.txt, trimmed to a
representative slice: the metadata block plus 11 document entries spanning
6 different categories). This is the part of the harvester that matters most
and the only part that could be validated against real content in the
environment this script was built in -- the live search-results pagination
(scrape_search_results) could not be exercised the same way and should be
smoke-tested with --limit before a full run.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from harvest_projects import parse_project_text

FIXTURE = Path(__file__).parent / "fixture_co_l1234.txt"


def test_metadata_fields():
    meta = parse_project_text(FIXTURE.read_text(encoding="utf-8"), "CO-L1234")
    assert meta["country"] == "Colombia", meta["country"]
    assert meta["approval_date"] == "July 31, 2018", meta["approval_date"]
    assert meta["project_status"] == "Closed", meta["project_status"]
    assert meta["lending_instrument"] == "Investment Loan", meta["lending_instrument"]
    print("metadata fields: OK ->", {k: v for k, v in meta.items() if k != "loan_proposal_docs"})


def test_loan_proposal_filter():
    meta = parse_project_text(FIXTURE.read_text(encoding="utf-8"), "CO-L1234")
    docs = meta["loan_proposal_docs"]
    filenames = {d["filename"] for d in docs}

    # The 5 "Loan Proposal"-category documents in the fixture must all be caught.
    expected = {
        "CO-L1234_Plan de Adquisiciones.xlsx",
        "CO-L1234 IGAS final_rev_07.11.DOCX",
        "Anexo IV_SPF y SSF post QRR.pdf",
        "Analisis economico del proyecto Rev NS 4JUL2018_limpia.docx",
        "Anexo tecnico.pdf",
        "CO-L1234 LP English.pdf",
    }
    assert filenames == expected, f"mismatch: got {filenames}"

    # Documents from OTHER categories (Loan Contract, Procurement Notices,
    # Environmental Impact Assessments, Project Profile, Financial Statement)
    # must NOT leak into the loan-proposal package.
    excluded = {
        "Estados Financieros Metro 2025.pdf",
        "Primera Operación Individual bajo la CCLIP para la primera Línea del Metro de Bogotá (PLMB) – Tramo 1.pdf",
        "CO-L1234 956.pdf",
        "ANEXO 9_EIAS.pdf",
        "CO-L1234_PP aprobado.pdf",
    }
    assert not (filenames & excluded), f"leaked non-LP docs: {filenames & excluded}"
    print(f"loan-proposal filter: OK -> {len(docs)}/11 fixture entries kept, all correctly categorized")


if __name__ == "__main__":
    test_metadata_fields()
    test_loan_proposal_filter()
    print("\nAll tests passed.")
