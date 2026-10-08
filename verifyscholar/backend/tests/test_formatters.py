from app.schemas import Author, PaperRecord, RetractionInfo
from app.services import formatters as f

REC = PaperRecord(
    doi="10.1038/nature14539", title="Deep learning", journal="Nature", year=2015, volume="521", issue="7553", pages="436-444",
    authors=[Author(family="LeCun", given="Yann"), Author(family="Bengio", given="Yoshua"), Author(family="Hinton", given="Geoffrey")],
    type="journal-article",
)


def test_apa():
    assert f.format_local(REC, "apa") == "LeCun, Y., Bengio, Y., & Hinton, G. (2015). Deep learning. Nature, 521(7553), 436–444. https://doi.org/10.1038/nature14539"


def test_ieee():
    out = f.format_local(REC, "ieee")
    assert out.startswith("Y. LeCun, Y. Bengio, and G. Hinton, “Deep learning,” Nature, vol. 521, no. 7553, pp. 436–444, 2015, doi: 10.1038/nature14539")


def test_vancouver_and_harvard_and_mla():
    assert f.format_local(REC, "vancouver").startswith("LeCun Y, Bengio Y, Hinton G. Deep learning. Nature. 2015;521(7553):436-444.")
    assert f.format_local(REC, "harvard").startswith("LeCun, Y., Bengio, Y. and Hinton, G. (2015) ‘Deep learning’, Nature, 521(7553), pp. 436–444.")
    mla = f.format_local(REC, "mla")
    assert mla.startswith("LeCun, Yann, et al. “Deep learning.” Nature, vol. 521, no. 7553, 2015, pp. 436-444.")


def test_bibtex_and_ris():
    key = f.bibtex_key(REC, set())
    assert key == "lecun2015deep"
    bib = f.to_bibtex(REC, key)
    assert bib.startswith("@article{lecun2015deep,") and "pages = {436--444}" in bib and "doi = {10.1038/nature14539}" in bib
    ris = f.to_ris(REC)
    assert "TY  - JOUR" in ris and "SP  - 436" in ris and "EP  - 444" in ris and ris.rstrip().endswith("ER  -")


def test_duplicate_bibtex_keys_get_suffix():
    used = set()
    assert f.bibtex_key(REC, used) != f.bibtex_key(REC, used)


async def test_export_text_numbered_and_removed():
    retracted = REC.model_copy(update={"retraction": RetractionInfo(status="retracted"), "doi": "10.1/x", "title": "Another paper"})
    bundle = f.ExportBundle(
        entries=[f.ExportEntry(REC, "verified", 1), f.ExportEntry(retracted, "review", 3)],
        removed=[f.RemovedEntry(2, "Fake, A. (2023). Made up.", "ghost", "No record found")],
    )
    text, media, name = await f.build_export(bundle, "text", "ieee", None)
    assert media == "text/plain" and name == "bibliography.txt"
    assert text.startswith("[1] Y. LeCun") and "[2] " in text
    assert "[RETRACTED]" in text and "[REVIEW" in text
    assert "Removed from this list" in text and "#2 [ghost] Fake, A." in text


async def test_export_apa_is_alphabetical_and_csv_has_removed_rows():
    b = REC.model_copy(update={"title": "Zebra paper", "authors": [Author(family="Abel", given="Z")], "year": 2001, "doi": None})
    bundle = f.ExportBundle(entries=[f.ExportEntry(REC, "verified", 1), f.ExportEntry(b, "extra")], removed=[f.RemovedEntry(2, "x", "ghost", "r")])
    text, _, _ = await f.build_export(bundle, "text", "apa", None)
    assert text.index("Abel") < text.index("LeCun")
    csv_text, media, _ = await f.build_export(bundle, "csv", "apa", None)
    assert media == "text/csv" and "removed,2,x" in csv_text
