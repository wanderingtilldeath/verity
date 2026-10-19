# Glossary

Use these terms exactly; they are how issues, tests and the README refer to things.

- **Article**: one indexed Wikipedia main-namespace, non-redirect page.
- **Lead**: the text before the first section heading, cleaned to plain text and capped at 5,000 characters. It is the only text we index.
- **Redirect query**: a redirect title used as a search query; its target Article is the only relevant document (known-item search).
- **Fingerprint**: one `(hash, position)` pair chosen by winnowing. The `fingerprints` index field holds the distinct hashes.
- **Winnowing**: the Schleimer–Wilkerson–Aiken fingerprint selection (MOSS): word 5-grams, hashed, minimum per window of 4. Guarantees detection of any shared run of at least 8 words.
- **Candidate**: an Article returned by the `terms_set` fingerprint query, before re-ranking.
- **Containment**: `|FP(query) ∩ FP(doc)| / |FP(query)|`, the primary plagiarism score. Not symmetric.
- **Passage**: a merged run of shared fingerprints, mapped to a character span on both the input and the source Article.
- **Transformation**: one way a plagiarism test case is derived from a source Article: `exact`, `light_edit`, `synonym`, `reorder`, `mixed`; negatives are `neg_unrelated` and `neg_same_topic`.
- **Held-out page**: a page deliberately never indexed (hash-selected at ingest); the source of negative cases.
- **Stage A / Stage B**: the Simple English dev corpus (~250k Articles) / the English leads corpus (>= 1.2M Articles).
- **Experiment index** (`articles_exp`): evaluation-only index with extra analyzer and BM25 subfields; never served by the API.
