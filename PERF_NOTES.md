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
- Question-level concurrency (worker still produces one question at a time).
- Azure budget raise: set AZURE_TRANSLATOR_CHARACTERS_PER_MINUTE for your plan tier.
- sleep_between_requests (1.5s) and Firestore get+set left as is.
- worker.py outer retry loop (5 attempts) unchanged; after bounded translation retries it can still
  regenerate the source, but only when translation fails 2 times in a row.
