# Normal generation speed-up notes

## What changed
- core/translation_service.py: LLM fallback translates languages in batches of 8 (was 4), up to 3 batches
  concurrently (ThreadPoolExecutor); results merged in batch/language order. A failed (truncated/invalid JSON)
  batch is split in half recursively before single-language calls. Omitted languages are still repaired
  (group, 3, 1) inside the same pool task. Validation and Azure-first / char-budget logic untouched.
- core/generator.py: once the source question passes validation, only translation is retried
  (bounded); source is regenerated only if translation still fails after those attempts.
- core/llm_manager.py: `generate_trivia` orders providers by rolling health (5 min window, last 20 calls:
  failure rate in 10% buckets, then mean latency in 2s buckets); cooling/disabled providers go last. Equally
  healthy providers still rotate (deterministic cursor). A timeout now stops the model loop of that provider
  (Groq, Cohere, Gemini; NVIDIA already did). 429 / cooldown (120s/300s) / "2nd 429 disables" unchanged, but concurrent 429s inside one cooldown window count once.

## Env knobs (default)
- TRANSLATION_LLM_BATCH_SIZE (8), TRANSLATION_LLM_WORKERS (3), TRANSLATION_STAGE_ATTEMPTS (2; 0.5-1s backoff;
  retries reuse the finished Azure stage, only Azure-failed languages go to the LLM)
- TRANSLATION_TOKENS_PER_LANGUAGE (450): translation calls pass max_output_tokens = clamp(langs*450, 4096..16000)
  to Groq/Cohere/NVIDIA (generation calls keep their old limits)
- GEMINI_TIMEOUT_MS (20000, was 25000), GROQ_TIMEOUT_SECONDS (15, was 20), COHERE_TIMEOUT_SECONDS (15, was 20),
  NVIDIA_TIMEOUT_SECONDS (30, unchanged)

## Expected effect
- 34 langs: ~4 LLM calls (3 concurrent) instead of 9 sequential; truncation costs log2 splits, not N singles.
- A translation hiccup no longer re-runs source generation (3x fewer source calls on that failure path).
- A slow/failing provider sinks in the order after 1-2 calls; a timeout costs one timeout, not 3-4.

## Deliberately NOT done
- Question-level concurrency (added later; see 'Max speed').
- Azure budget raise: set AZURE_TRANSLATOR_CHARACTERS_PER_MINUTE for your plan tier.
- sleep_between_requests (1.5s) and Firestore get+set left as is.
- worker.py outer retry loop (5 attempts) unchanged; after bounded translation retries it can still
  regenerate the source, but only when translation fails 2 times in a row.

## Max speed (test runs)
Knobs (all default to today's behaviour; `run_ui_fast.bat` sets them for one window only):
- `GENERATION_CONCURRENCY` (1; clamped 1..6) / payload key `concurrency` / `GenerationOptions.concurrency`:
  questions generated in parallel. With >1 the default `sleep_between_requests` drops 1.5 -> 0.3 (explicit values kept).
- `TRANSLATION_USE_AZURE=0` (Azure's 30k chars/min budget caps global questions at ~2/min), `TRANSLATION_LLM_WORKERS=4`,
  `TRANSLATION_LLM_BATCH_SIZE=8`.
- Concurrency design: a credit slot is reserved per in-flight question and consumed only on save (duplicate/failure
  frees it); planning, balancers, counters, `save_worker_question` (check+write) and event emission run under one lock;
  retry/backoff is per thread; a fatal error in any worker stops the others and is re-raised. Visual-question slots are
  never run in parallel (they need the in-order schedule). Writes gate (`KNOWLEY_GENERATION_WRITES_ENABLED`) unchanged.

`python scripts/bench_generation.py` (simulation: real `run_generation` + real translation batching, mocked providers,
LLM src 6 s, translation 8 s/batch, Azure 1.5 s + 30k chars/min, 3% 429 / 2% timeout, time-scale 0.02; 20 global + 20 local):

| configuration | total s | s/question | speedup |
|---|---|---|---|
| OLD-equivalent (conc 1, batch 4, workers 1, Azure on) | 1157 | 28.9 | 1.00x |
| CURRENT main (batch 8, workers 3, Azure on) | 1157 | 28.9 | 1.00x |
| AZURE-OFF (batch 8, workers 3) | 940 | 23.5 | 1.23x |
| AZURE-OFF + concurrency 4 (workers 4) | 224 | 5.6 | 5.17x |

Caveats: simulated latencies, not live; visuals are off in all simulated scenarios. The OLD == CURRENT 1.00x row is a simulation
artefact: simulated Azure never fails, so LLM batching never runs; the earlier batching speedup applies to real-world Azure
failures/429s/timeouts, not to this table. Real gains are capped by free-tier LLM rate limits (429s / provider cooldowns will eat
into concurrency 4; watch `retry` and `provider_unavailable` events). `--live N --dry-run` measures the real thing
with writes off (refuses if `KNOWLEY_GENERATION_WRITES_ENABLED` is set).
