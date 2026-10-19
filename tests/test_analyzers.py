import pytest

pytestmark = pytest.mark.es


def tokens(es, index, **kw):
    return [t["token"] for t in es.indices.analyze(index=index, **kw)["tokens"]]


def test_possessive_is_stripped(es, fixture_alias):
    toks = tokens(es, fixture_alias, analyzer="wiki_text", text="Einstein's theories")
    assert "einstein" in toks and "einstein's" not in toks and "s" not in toks


def test_stopwords_removed_and_lowercased(es, fixture_alias):
    toks = tokens(es, fixture_alias, analyzer="wiki_text", text="The Origin of the Species")
    assert "the" not in toks and "of" not in toks
    assert "origin" in toks


def test_plural_is_stemmed(es, fixture_alias):
    assert (
        tokens(es, fixture_alias, analyzer="wiki_text", text="rivers")[0]
        == tokens(es, fixture_alias, analyzer="wiki_text", text="river")[0]
    )


def test_asciifolding_keeps_original(es, fixture_alias):
    toks = tokens(es, fixture_alias, analyzer="wiki_text", text="café")
    assert "cafe" in toks and "café" in toks


def test_title_exact_normalizer(es, fixture_alias):
    resp = es.indices.analyze(index=fixture_alias, normalizer="lowercase_norm", text="Café CULTURE")
    assert [t["token"] for t in resp["tokens"]] == ["cafe culture"]


def test_search_as_you_type_shingles(es, fixture_alias):
    toks = tokens(es, fixture_alias, field="title.suggest._2gram", text="quick brown fox")
    assert toks == ["quick brown", "brown fox"]


def test_mapping_is_strict_and_records_winnowing_params(es, fixture_alias):
    mapping = next(iter(es.indices.get_mapping(index=fixture_alias).values()))["mappings"]
    assert mapping["dynamic"] == "strict"
    assert mapping["_meta"] == {"winnow_k": 5, "winnow_w": 4, "hash_bits": 64}
