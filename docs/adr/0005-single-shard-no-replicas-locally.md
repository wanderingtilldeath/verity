# 5. One shard, zero replicas on the local node

Status: accepted

A single node cannot allocate replicas (the cluster would stay yellow). One shard avoids per-shard IDF
differences in BM25, so relevance numbers are reproducible. A multi-node deployment would shard once a
shard grows past tens of GB. Sets the scale limits of every number in the README.
