# Performance test plan and baseline

## Workload

`tests/performance/locustfile.py` represents authenticated users doing identity reads (5), document listing (3), grounded RAG questions (2), and synthetic PDF upload plus ingestion (1). Authentication occurs once for each simulated user. Upload/process tasks run only for CSV profiles marked `can_upload=true`; use admin users for those profiles and employees for ordinary reads. For multi-tenant runs, set `VAULTRAG_USERS_CSV` to a CSV with `tenant_id,email,password,can_upload` columns containing users from both synthetic tenants. Never use real user or document data.

Example PowerShell command:

```powershell
$env:VAULTRAG_TENANT_ID = '<synthetic-tenant-uuid>'
$env:VAULTRAG_USER_EMAIL = '<synthetic-user-email>'
$env:VAULTRAG_USER_PASSWORD = '<synthetic-password>'
$env:VAULTRAG_CAN_UPLOAD = 'true' # admin identity only; omit for employees
locust -f .\tests\performance\locustfile.py --host http://127.0.0.1:3000 --users 5 --spawn-rate 1 --run-time 2m --headless --csv .\artifacts\baseline
```

The front proxy exposes API paths under `/api`; the load test defaults to that prefix. For a directly exposed backend URL such as `http://127.0.0.1:8000`, set `$env:VAULTRAG_API_PREFIX = ''`. Record which target was used.

Locust records login, reads, upload, ingestion processing and RAG endpoint distributions (mean, median, p95/p99, throughput and failures) in its CSV/HTML report. Backend logs emit privacy-limited `RAG stage=complete` durations for query embedding, combined retrieval/authorization, reranking, context construction, LLM generation and citation validation. Successful ingestion logs separately report claim, PDF extraction, text cleaning, chunking, embedding and chunk persistence durations; neither path logs prompt or document text. Capture host/container CPU and memory with `docker stats --no-stream` during each run. For query-level database duration, enable reviewed PostgreSQL query statistics in the isolated test/demo database and report its configuration; do not infer a database duration by subtracting other timings.

## Baseline measurements

Measured locally on 2026-11-12 using Docker Desktop Linux containers on a
Windows host. Docker reported 12 CPUs and a 7.573 GiB container memory limit.
The configured synthetic users were used; no real user or document data was
loaded.

### Direct API baseline

This run bypassed Nginx to measure application/API behavior with two users,
one-user-per-second ramp-up, and a 30-second duration. RAG and upload tasks were
disabled.

| Endpoint | Requests | Failures | Mean ms | Median ms | P95 ms | P99 ms | Requests/s |
|---|---:|---:|---:|---:|---:|---:|---:|
| POST `/auth/login` | 2 | 0 | 95.4 | 85.2 | 105.6 | 105.6 | 0.07 |
| GET `/auth/me` | 11 | 0 | 8.2 | 7.0 | 16.7 | 16.7 | 0.39 |
| GET `/documents` | 19 | 0 | 12.4 | 10.0 | 21.3 | 21.3 | 0.67 |
| **Aggregated** | **32** | **0** | **16.1** | **10.0** | **85.2** | **110.0** | **1.13** |

### Proxy/RAG workload

The configured two-minute, five-user Compose performance profile produced 87
requests with 25 failures. It measured login, identity reads, document reads
and RAG requests through Nginx. RAG had mean latency 30.6 seconds and a 72
second maximum; five RAG requests returned HTTP 503. Twenty identity requests
returned HTTP 429 because all Locust users shared the performance container's
source IP and therefore shared Nginx's per-IP rate-limit bucket.

This is a measured operational limitation, not a successful capacity result.
The load profile must be rerun with a distributed limiter strategy or a
documented test bypass before using it as a production capacity benchmark.

| Metric | Measured value |
|---|---:|
| Total requests | 87 |
| Total failures | 25 (28.7%) |
| Aggregated mean | 4595.8 ms |
| Aggregated median | 19 ms |
| Aggregated p95 | 61,000 ms |
| Aggregated p99 | 72,000 ms |
| Aggregated throughput | 0.74 requests/s |
| RAG mean / median | 30,615.7 / 9,700 ms |
| RAG failures | 5 HTTP 503 |
| Proxy identity failures | 20 HTTP 429 |

The raw Locust CSV files are in
`docs/performance/results/` and must be retained with the environment
configuration used for each future run.

No performance numbers are inferred from unit tests. The model needs to be explicitly pulled into the persistent Ollama volume before a real RAG benchmark. Compare total query latency with component timings from application telemetry; Locust end-to-end totals alone cannot distinguish retrieval from model generation. Track ingestion stage durations and failed-job counts separately. Do not claim a throughput target until measured on declared hardware.

## Bottleneck hypotheses to validate

1. Cold sentence-transformer model initialization and CPU inference can dominate first-query latency.
2. Ollama token generation is likely to dominate fully grounded question latency on CPU; record tokens/second separately.
3. PDF extraction/embedding and pgvector writes can bottleneck concurrent ingestion; currently processing is synchronous at the API boundary.
4. Database connection pool size and pgvector index plan need measurement at representative chunk counts.

During the baseline capture, `docker stats --no-stream` reported approximately
148.4 MiB API memory, 59.4 MiB database memory, 34.6 MiB Ollama memory and
14.5 MiB frontend memory at idle. These are point-in-time observations, not
peak measurements. PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` and sustained CPU
profiles were not captured and remain required before optimization.
