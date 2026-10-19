# 6. Winnowing parameters and hash

Status: accepted

k = 5 words, w = 4, guarantee threshold k + w - 1 = 8 words. 64-bit blake2b of the joined k-gram, stored
as 16 hex chars in a `keyword` field. Python's builtin `hash()` is salted per process and must never be
used. The parameters are recorded in the index `_meta`; `/healthz` fails if code and index disagree.
Stopwords are not removed before winnowing (that would break the run-length guarantee).
