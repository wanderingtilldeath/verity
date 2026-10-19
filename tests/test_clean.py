from ingest import clean
from ingest.docs import Config, is_heldout, make_doc
from ingest.dump_reader import Page

WIKITEXT = """{{Infobox scientist|name=Ada}}
{{Short description|English mathematician}}
'''Ada Lovelace''' (1815-1852) was an [[England|English]] [[mathematician]] and writer, chiefly known
for her work on Charles Babbage's proposed [[Analytical Engine]].<ref>Some citation here</ref>
[[File:Ada.jpg|thumb|Portrait of Ada]]
She was the first to recognise that the machine had applications beyond pure calculation, and
published the first algorithm intended to be carried out by such a machine, so she is often
regarded as the first computer programmer in history.

== Early life ==
She was born in London and this sentence must not appear in the lead.
"""


def test_lead_is_cut_at_first_heading():
    assert "Early life" not in clean.lead_wikitext(WIKITEXT)
    assert "must not appear" not in clean.lead_wikitext(WIKITEXT)


def test_clean_lead_strips_markup():
    text = clean.clean_lead(WIKITEXT)
    assert text.startswith("Ada Lovelace (1815-1852) was an English mathematician")
    for junk in ("{{", "}}", "[[", "]]", "<ref", "File:", "thumb", "Infobox", "'''", "citation"):
        assert junk not in text
    assert "first computer programmer" in text


def test_truncate_on_word_boundary():
    out = clean.truncate("word " * 5000, 100)
    assert len(out) <= 100 and not out.endswith("wor")


def test_disambiguation_detection():
    assert clean.is_disambiguation("Mercury", "'''Mercury''' may refer to:\n{{disambiguation}}")
    assert clean.is_disambiguation("Mercury (disambiguation)", "x")
    assert not clean.is_disambiguation("Mercury", "Mercury is a planet.")


def test_lead_from_plain_takes_first_paragraphs():
    text = "\n\n".join(f"Paragraph {i} " + "word " * 100 for i in range(10))
    lead = clean.lead_from_plain(text)
    assert "Paragraph 0" in lead and "Paragraph 9" not in lead


def _page(**kw):
    base = dict(page_id=1, title="Ada Lovelace", ns=0, redirect=None, text=WIKITEXT)
    return Page(**{**base, **kw})


def test_make_doc_ok_has_fingerprints_and_url():
    status, doc = make_doc((_page(), Config("xml", "en")))
    assert status == "ok"
    assert doc["fingerprints"] and doc["url"] == "https://en.wikipedia.org/wiki/Ada_Lovelace"


def test_make_doc_skips():
    cfg = Config("xml", "en")
    assert make_doc((_page(ns=14), cfg))[0] == "skip_namespace"
    assert make_doc((_page(redirect="Other"), cfg))[0] == "skip_redirect"
    assert make_doc((_page(text="{{disambiguation}} short"), cfg))[0] == "skip_disambiguation"
    assert make_doc((_page(text="Too short."), cfg))[0] == "skip_short"


def test_heldout_split_is_deterministic_and_roughly_the_right_size():
    ids = range(1, 50_001)
    a = [i for i in ids if is_heldout(i, 0.01)]
    assert a == [i for i in ids if is_heldout(i, 0.01)]
    assert 300 < len(a) < 700
    status, doc = make_doc((_page(page_id=a[0]), Config("xml", "en", heldout_frac=0.01)))
    assert status == "heldout" and "fingerprints" not in doc
