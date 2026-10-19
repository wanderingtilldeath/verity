# 1. Index the lead section only, same shape for both stages

Status: accepted

The 250k and 1.2M latency numbers must describe the same kind of document, and 1.2M full articles would
need tens of GB. Every document is `title` + lead `text` (<= 5,000 chars). Consequences: smaller index,
faster ingest, plagiarism only matches lead text, and informational queries about deep content are not
answerable (stated in the README limits). Stage B lead extraction differs slightly by source (heading
cut on wikitext vs. first paragraphs of pre-cleaned text); both are capped identically.
