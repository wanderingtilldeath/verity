"""A small deterministic corpus: 12 hand-written articles plus seeded filler so ranking is non-trivial."""

from __future__ import annotations

import random

from app.winnowing import fingerprint_hashes

ARTICLES: list[tuple[int, str, str]] = [
    (
        1,
        "Albert Einstein",
        "Albert Einstein was a German-born theoretical physicist who developed the theory of relativity, "
        "one of the two pillars of modern physics. His work is also known for its influence on the "
        "philosophy of science. He received the 1921 Nobel Prize in Physics for his services to "
        "theoretical physics, and especially for his discovery of the law of the photoelectric effect, "
        "a pivotal step in the development of quantum theory.",
    ),
    (
        2,
        "Python (programming language)",
        "Python is a high-level, general-purpose programming language. Its design philosophy emphasizes "
        "code readability with the use of significant indentation. Python is dynamically typed and "
        "garbage-collected. It supports multiple programming paradigms, including structured, "
        "object-oriented and functional programming, and is often described as a language with "
        "batteries included because of its comprehensive standard library.",
    ),
    (
        3,
        "Marie Curie",
        "Marie Sklodowska Curie was a Polish and naturalised-French physicist and chemist who conducted "
        "pioneering research on radioactivity. She was the first woman to win a Nobel Prize, the first "
        "person to win a Nobel Prize twice, and the only person to win a Nobel Prize in two different "
        "scientific fields. She discovered the elements polonium and radium with her husband Pierre.",
    ),
    (
        4,
        "Cafe culture",
        "A cafe is a small restaurant selling light meals and drinks, especially coffee. The word cafe "
        "comes from the French word for coffee, written café with an accent. Cafe culture spread "
        "across Europe in the seventeenth century, when coffee houses became places for conversation, "
        "reading newspapers and political debate among merchants, writers and students.",
    ),
    (
        5,
        "Mount Everest",
        "Mount Everest is Earth's highest mountain above sea level, located in the Mahalangur Himal "
        "sub-range of the Himalayas. The China-Nepal border runs across its summit point. Its elevation "
        "of 8,849 metres was most recently established in 2020. Climbers approach it from Nepal in the "
        "south east or from Tibet in the north, and many expeditions attempt the summit every spring.",
    ),
    (
        6,
        "Photosynthesis",
        "Photosynthesis is the process by which plants, algae and some bacteria convert light energy "
        "into chemical energy stored in glucose. It takes place mostly in the chloroplasts of leaf "
        "cells, where chlorophyll absorbs sunlight. Carbon dioxide and water are consumed and oxygen is "
        "released as a by-product, which is why photosynthesis is essential for life on Earth.",
    ),
    (
        7,
        "Great Wall of China",
        "The Great Wall of China is a series of fortifications built across the historical northern "
        "borders of ancient Chinese states to protect against nomadic invasions. Several walls were "
        "built from as early as the seventh century BC, and the best known sections were built by the "
        "Ming dynasty, using stone, brick, tamped earth and wood along mountain ridges.",
    ),
    (
        8,
        "Jazz",
        "Jazz is a music genre that originated in the African-American communities of New Orleans in "
        "the late nineteenth and early twentieth centuries. It is characterised by swing and blue "
        "notes, call and response vocals, polyrhythms and improvisation. Jazz has roots in blues and "
        "ragtime, and became a major form of musical expression across the world.",
    ),
    (
        9,
        "Amazon River",
        "The Amazon River in South America is the largest river by discharge volume of water in the "
        "world. It flows through Peru, Colombia and Brazil before reaching the Atlantic Ocean. The "
        "surrounding basin holds the largest tropical rainforest on the planet, which shelters an "
        "enormous number of plant and animal species, many of them found nowhere else.",
    ),
    (
        10,
        "Printing press",
        "The printing press is a mechanical device for applying pressure to an inked surface resting "
        "upon a print medium, thereby transferring the ink. Johannes Gutenberg introduced movable type "
        "printing in Europe around 1440. The invention made books cheaper, spread literacy and "
        "accelerated the circulation of scientific and religious ideas across the continent.",
    ),
    (
        11,
        "Theory of evolution",
        "Evolution is change in the heritable characteristics of biological populations over successive "
        "generations. Charles Darwin described natural selection in his book On the Origin of Species, "
        "published in 1859. Individuals with traits better suited to their environment tend to survive "
        "and reproduce, so those traits become more common in later generations of the population.",
    ),
    (
        12,
        "Machine learning",
        "Machine learning is a field of study in artificial intelligence concerned with statistical "
        "algorithms that can learn from data and generalise to unseen examples. Neural networks, "
        "decision trees and support vector machines are common model families. Training usually means "
        "minimising a loss function over a dataset, then evaluating the result on held-out data.",
    ),
]

_VOCAB = (
    "river stone market harvest lantern copper valley anchor garden winter orchard bridge falcon "
    "meadow harbor candle fabric quarry timber saddle thunder ribbon granite pepper velvet marble "
    "compass sparrow glacier lagoon cinder prairie tavern basalt trellis meridian cobalt walnut"
).split()


def filler(n: int = 40, seed: int = 1) -> list[tuple[int, str, str]]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        words = [rng.choice(_VOCAB) for _ in range(80)]
        out.append((1000 + i, f"Filler {rng.choice(_VOCAB)} {i}", " ".join(words)))
    return out


def make_docs() -> list[dict]:
    docs = []
    for pid, title, text in ARTICLES + filler():
        docs.append(
            {
                "page_id": pid,
                "title": title,
                "text": text,
                "url": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                "fingerprints": fingerprint_hashes(text),
            }
        )
    return docs
