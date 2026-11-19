"""Knowledge-graph services.

Layering
--------
``normalization``   pure text rules (no I/O)
``extraction``      rules, optional spaCy, optional Ollama + validation
``entity_resolution`` alias/anchor based matching, reviews for ambiguity
``repository``      persistence helpers
``authorization``   evidence-gated visibility through the M2 engine
``traversal``       bounded traversal over an already-authorized subgraph
``retrieval``       seeds + traversal + candidate chunks for RAG
``conflicts``       contradiction detection over visible evidence
``jobs``            durable, idempotent extraction jobs
``analytics``       privacy-limited graph query metrics

The invariant every module respects: a node, edge, alias or count is only ever
returned after at least one of its evidence rows points at a document the caller
may currently read.
"""
