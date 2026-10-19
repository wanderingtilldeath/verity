# 2. Measure 250k and 1.2M docs on one index

Status: accepted

Ingest Stage B in a fixed order, pause at 250k (`--pause-at`), force-merge to one segment, benchmark,
resume to 1.2M, force-merge again, benchmark again. Identical documents and segment layout, one ingest.
Rejected: two separate indexes (double the ingest, and different data unless carefully sampled).
