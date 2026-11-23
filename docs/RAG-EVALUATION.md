# RAG Evaluation

The authoritative implementation and methodology document is
[evaluation/EVALUATION_FRAMEWORK.md](evaluation/EVALUATION_FRAMEWORK.md). This
page is the entry point required by the Milestone 10 documentation checklist.

## Dataset

- Synthetic, versioned corpus bundled with the project; every gold chunk belongs
  to a document the evaluation user may read.
- Cases cover single- and multi-document answers, insufficient evidence,
  conflicts, restricted documents, permission changes and document versions.
- No customer or confidential content is included.

## Configurations

Semantic, keyword, hybrid, hybrid + rerank, hybrid + graph and
hybrid + graph + rerank. Each run stores its configuration and top K.

## Metrics

Recall@K, Precision@K, MRR, NDCG@K, retrieval success rate and relevant-evidence
rate; deterministic answerability, citation coverage, citation validity and
unsupported-claim indicators. Answer relevance/correctness is reported only when
a labeled reference exists. The optional Ollama judge is off by default, records
model and prompt version, and is labeled model-based — it is not a deterministic
metric.

## Reproduction

Run the API, open `/admin/evaluation`, import the synthetic dataset, start a run
and open its results. API tests exercise the same path:

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest app/tests/test_evaluation_api.py -q
```

The evaluation tests use a deterministic fake embedding/LLM so metrics are
reproducible. The full backend suite passed with 368 tests at the Milestone 10
release candidate.

## Measured results

No production-corpus benchmark or latency study was executed for this milestone.
The dashboard shows metrics for runs an operator actually performs; no accuracy
number is hardcoded or claimed in documentation. Configure real embeddings/LLM
and run the evaluation against your own corpus before drawing quality
conclusions.

## Limitations

- Synthetic corpus is small and not representative of every domain.
- Deterministic metrics depend on gold chunk labels; labeling errors propagate.
- LLM-judge results vary by model and are explicitly non-deterministic.
- Authorization violations must remain zero; a run with any violation is flagged.
