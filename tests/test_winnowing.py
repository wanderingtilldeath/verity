import os
import random
import subprocess
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

from app.winnowing import (
    GUARANTEE_WORDS,
    K,
    W,
    _hash_gram,
    align,
    containment,
    fingerprint_hashes,
    fingerprints,
    jaccard,
    tokenize,
    winnow,
)

VOCAB = [f"w{i}" for i in range(5000)]


def rand_words(rng: random.Random, n: int) -> list[str]:
    return [rng.choice(VOCAB) for _ in range(n)]


def fp_set(words: list[str]) -> set[str]:
    return set(fingerprint_hashes(" ".join(words)))


# ---- determinism ------------------------------------------------------------------------------


def test_same_text_same_fingerprints():
    text = " ".join(rand_words(random.Random(1), 200))
    assert fingerprints(text) == fingerprints(text)


def test_stable_across_processes_and_hash_seeds():
    """The builtin hash() is salted per process; fingerprints must not depend on it."""
    code = (
        "from app.winnowing import fingerprint_hashes;"
        "print(','.join(fingerprint_hashes('the quick brown fox jumps over the lazy dog again and again today')))"
    )
    outs = {
        subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout
        for seed in ("0", "1", "12345")
    }
    assert len(outs) == 1


def test_case_whitespace_punctuation_invariant():
    a = "The quick brown fox, jumps over the lazy dog; again and again - today!"
    b = "the   QUICK brown fox jumps over the lazy dog again and again today"
    assert fingerprint_hashes(a) == fingerprint_hashes(b)


def test_offsets_point_at_original_text():
    text = "Hello,  World! Foo-bar"
    toks = tokenize(text)
    assert [text[t.start : t.end] for t in toks] == ["Hello", "World", "Foo", "bar"]
    assert [t.word for t in toks] == ["hello", "world", "foo", "bar"]


# ---- the guarantee ----------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(
    seed=st.integers(0, 10**6),
    left_a=st.integers(0, 40),
    right_a=st.integers(0, 40),
    left_b=st.integers(0, 40),
    right_b=st.integers(0, 40),
)
def test_shared_run_of_k_plus_w_minus_1_words_is_always_detected(
    seed, left_a, right_a, left_b, right_b
):
    rng = random.Random(seed)
    shared = rand_words(rng, GUARANTEE_WORDS)
    a = rand_words(rng, left_a) + shared + rand_words(rng, right_a)
    b = rand_words(rng, left_b) + shared + rand_words(rng, right_b)
    assert fp_set(a) & fp_set(b)


def test_shorter_shared_runs_can_be_missed():
    """Documents the bound: with k+w-2 shared words there exist contexts with no common fingerprint."""
    rng = random.Random(7)
    misses = 0
    for _ in range(2000):
        shared = rand_words(rng, GUARANTEE_WORDS - 1)
        a = rand_words(rng, 20) + shared + rand_words(rng, 20)
        b = rand_words(rng, 20) + shared + rand_words(rng, 20)
        misses += not (fp_set(a) & fp_set(b))
    assert misses > 0


# ---- noise tolerance --------------------------------------------------------------------------


def test_one_word_substitution_touches_at_most_k_grams():
    rng = random.Random(3)
    words = rand_words(rng, 120)
    edited = words[:]
    edited[60] = "SUBSTITUTED"
    grams = lambda ws: [_hash_gram(ws[i : i + K]) for i in range(len(ws) - K + 1)]  # noqa: E731
    changed = sum(x != y for x, y in zip(grams(words), grams(edited), strict=True))
    assert changed == K


def test_light_edit_keeps_most_fingerprints():
    rng = random.Random(5)
    words = rand_words(rng, 150)
    edited = words[:]
    edited[75] = "SUBSTITUTED"
    a, b = fp_set(words), fp_set(edited)
    assert containment(b, a) >= 0.6


def test_unrelated_texts_do_not_match():
    rng = random.Random(9)
    assert containment(fp_set(rand_words(rng, 300)), fp_set(rand_words(rng, 300))) == 0.0


# ---- shape ------------------------------------------------------------------------------------


def test_fingerprint_density_is_close_to_two_over_w_plus_one():
    text = " ".join(rand_words(random.Random(11), 20_000))
    n_grams = 20_000 - K + 1
    density = len(winnow(tokenize(text))) / n_grams
    assert abs(density - 2 / (W + 1)) < 0.03


def test_positions_are_increasing_and_in_range():
    fps = fingerprints(" ".join(rand_words(random.Random(2), 300)))
    pos = [f.pos for f in fps]
    assert pos == sorted(set(pos))
    assert pos[-1] <= 300 - K


def test_edge_cases():
    assert fingerprints("") == []
    assert fingerprints("one two three four") == []  # fewer than k words
    assert len(fingerprints("one two three four five")) == 1  # one gram, one window
    assert len(fingerprints("one two three four five six seven")) >= 1  # fewer than w grams
    assert fingerprint_hashes("café über straße naïve résumé façade") != []
    repeated = "go " * 200
    assert (
        len(fingerprint_hashes(repeated)) == 1
    )  # equal hashes collapse to one distinct fingerprint


def test_containment_and_jaccard():
    assert containment(set(), {"a"}) == 0.0
    assert containment({"a", "b"}, {"a", "c"}) == 0.5
    assert jaccard({"a", "b"}, {"a", "c"}) == 1 / 3
    assert jaccard(set(), set()) == 0.0


# ---- alignment --------------------------------------------------------------------------------


def test_align_finds_copied_block_and_maps_both_sides():
    rng = random.Random(21)
    block = rand_words(rng, 40)
    doc = rand_words(rng, 30) + block + rand_words(rng, 30)
    query = rand_words(rng, 10) + block + rand_words(rng, 10)
    passages = align(fingerprints(" ".join(query)), fingerprints(" ".join(doc)))
    assert len(passages) == 1
    p = passages[0]
    # the passage lies inside the copied block on both sides
    assert 10 <= p.q_start and p.q_end <= 50 + 1
    assert 30 <= p.d_start and p.d_end <= 70 + 1
    assert p.words >= 30


def test_align_splits_reordered_blocks():
    rng = random.Random(22)
    a, b = rand_words(rng, 40), rand_words(rng, 40)
    doc = a + rand_words(rng, 20) + b
    query = b + rand_words(rng, 20) + a
    passages = align(fingerprints(" ".join(query)), fingerprints(" ".join(doc)))
    assert len(passages) >= 2
