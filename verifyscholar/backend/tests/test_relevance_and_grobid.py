import httpx
import pytest

from app.config import get_settings
from app.schemas import CitationResult, PaperRecord, ParsedCitation
from app.services import embeddings, relevance
from app.services.grobid import GrobidClient, GrobidError, parse_citation_list_tei, parse_fulltext_tei

TEI = b"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
 <teiHeader><fileDesc><titleStmt><title level="a" type="main">Transformer models for sentiment analysis in Hindi</title></titleStmt></fileDesc>
  <profileDesc><abstract><div><p>We study transformer based neural language models for sentiment classification of Hindi social media text.</p></div></abstract></profileDesc></teiHeader>
 <text xml:lang="en"><body>
   <div><head n="1">Introduction</head>
    <p><s>Transformer language models achieve strong results in sentiment classification of Hindi text <ref type="bibr" target="#b0">[1]</ref>.</s>
       <s>Convolutional networks were popular earlier <ref type="bibr" target="#b1">[2]</ref>, and cooking recipes are unrelated <ref type="bibr" target="#b9">[10]</ref>.</s></p>
   </div></body>
  <back><div type="references"><listBibl>
   <biblStruct xml:id="b0"><analytic><title level="a" type="main">Hindi sentiment classification with transformer language models</title>
     <author><persName><forename type="first">Rahul</forename><surname>Kumar</surname></persName></author>
     <author><persName><forename type="first">Priya</forename><surname>Singh</surname></persName></author>
     <idno type="DOI">10.1234/hindi.2022.1</idno></analytic>
     <monogr><title level="j">Journal of Language Models</title><imprint><biblScope unit="volume">34</biblScope><biblScope unit="issue">5</biblScope><biblScope unit="page" from="1234" to="1245"/><date type="published" when="2022-05-01"/></imprint></monogr>
     <note type="raw_reference">Kumar R, Singh P. Hindi sentiment classification with transformer language models. J Lang Models 2022;34(5):1234-45.</note></biblStruct>
   <biblStruct xml:id="b1"><analytic><title level="a" type="main">A history of convolutional networks</title><author><persName><forename>Ann</forename><surname>Lee</surname></persName></author></analytic>
     <monogr><title level="j">Vision Letters</title><imprint><date when="2015"/></imprint></monogr></biblStruct>
   <biblStruct xml:id="b2"><analytic><title level="a" type="main">An orphan reference nobody cites</title></analytic><monogr><title level="j">Nowhere</title><imprint><date when="2001"/></imprint></monogr></biblStruct>
  </listBibl></div></back></text></TEI>"""


def test_parse_fulltext_tei():
    doc = parse_fulltext_tei(TEI)
    assert doc.title.startswith("Transformer models") and "Hindi social media" in doc.abstract
    assert [r.title for r in doc.references][0] == "Hindi sentiment classification with transformer language models"
    r0 = doc.references[0]
    assert r0.doi == "10.1234/hindi.2022.1" and r0.year == 2022 and r0.volume == "34" and r0.issue == "5" and r0.pages == "1234-1245"
    assert [a.family for a in r0.authors] == ["Kumar", "Singh"] and r0.journal == "Journal of Language Models" and r0.ref_id == "b0"
    assert r0.raw.startswith("Kumar R, Singh P.")
    assert len(doc.mentions) == 2 and doc.mentions[0].ref_id == "b0" and "Hindi text" in doc.mentions[0].sentence
    assert doc.unmatched_markers == ["[10]"]


def test_parse_citation_list_tei_keeps_pasted_text():
    xml = b"""<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body><listBibl>
      <biblStruct><analytic><title level="a" type="main">Deep learning</title></analytic><monogr><title level="j">Nature</title><imprint><date when="2015"/></imprint></monogr></biblStruct>
    </listBibl></body></text></TEI>"""
    out = parse_citation_list_tei(xml, ["LeCun Y. Deep learning. Nature 2015 doi:10.1038/nature14539", "second raw"])
    assert out[0].title == "Deep learning" and out[0].doi == "10.1038/nature14539" and out[0].raw.startswith("LeCun")
    assert out[1].parse_method == "heuristic"


async def test_grobid_client_posts_expected_parameters():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(request.url), request.content.decode("latin-1")
        return httpx.Response(200, content=TEI)

    c = GrobidClient(get_settings())
    c._transport = httpx.MockTransport(handler)
    doc = await c.process_fulltext(b"%PDF-1.4 fake", "paper.pdf")
    assert seen["url"].endswith("/api/processFulltextDocument")
    for expected in ('name="consolidateCitations"', "segmentSentences", "includeRawCitations"):
        assert expected in seen["body"]
    assert 'name="consolidateCitations"\r\n\r\n0' in seen["body"]  # consolidation stays off on purpose
    assert len(doc.references) == 3


async def test_grobid_errors_are_reported_clearly():
    c = GrobidClient(get_settings())
    c._transport = httpx.MockTransport(lambda r: httpx.Response(204))
    with pytest.raises(GrobidError, match="scanned"):
        await c.process_fulltext(b"%PDF")
    c._transport = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")))
    with pytest.raises(GrobidError, match="Could not reach GROBID"):
        await c.process_citation_list(["x"])
    assert await c.is_alive() is False


def test_tfidf_ranks_related_text_higher():
    b = embeddings.TfidfBackend()
    related, unrelated = b.score_pairs([
        ("Transformer language models for sentiment classification of Hindi text", "Hindi sentiment classification with transformer language models. We fine-tune transformers for sentiment."),
        ("Transformer language models for sentiment classification of Hindi text", "Baking sourdough bread: hydration levels, fermentation time and oven spring."),
    ])
    assert related > 0.3 > unrelated


def test_label_table():
    f = relevance.combined_label
    assert f(0.5, 0.5, 0.4, 0.3) == "well_supported"
    assert f(0.1, 0.5, 0.4, 0.3) == "possibly_misattributed"
    assert f(0.5, 0.1, 0.4, 0.3) == "background"
    assert f(0.1, 0.1, 0.4, 0.3) == "needs_review"
    assert f(None, 0.5, 0.4, 0.3) is None


async def test_analyze_document_flags_orphans_and_scores():
    embeddings.reset_backend()
    doc = parse_fulltext_tei(TEI)
    results = []
    for p in doc.references:
        rec = None
        if p.ref_id == "b0":
            rec = PaperRecord(doi=p.doi, title=p.title, abstract="We fine-tune transformer language models for sentiment classification of Hindi text and report strong results.")
        if p.ref_id == "b1":
            rec = PaperRecord(title=p.title, abstract="Baking sourdough bread requires patience, flour, water and a lively starter culture.")
        if p.ref_id == "b2":
            rec = PaperRecord(title=p.title, abstract="Nobody cites this paper about deep sea fish.")
        results.append(CitationResult(index=p.index, raw=p.raw, parsed=p, verdict="verified" if rec else "ghost", record=rec))
    info = await relevance.analyze(doc, results)
    by = {r.parsed.ref_id: r.relevance for r in results}
    assert info.embedding_backend == "tfidf"
    assert by["b0"].status == "assessed" and by["b0"].label == "well_supported" and by["b0"].mentions == 1
    assert by["b1"].label == "needs_review"  # cited for convolutional nets but the abstract is about baking
    assert by["b2"].orphan and by["b2"].label is None and info.orphan_indices == [3]
    assert info.unmatched_markers == ["[10]"]


async def test_citation_list_request_repeats_the_form_field():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        return httpx.Response(200, content=b'<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body><listBibl><biblStruct/><biblStruct/></listBibl></body></text></TEI>')

    c = GrobidClient(get_settings())
    c._transport = httpx.MockTransport(handler)
    out = await c.process_citation_list(["first reference", "second reference"])
    assert seen["body"].count("citations=") == 2 and "consolidateCitations=0" in seen["body"]
    assert len(out) == 2
