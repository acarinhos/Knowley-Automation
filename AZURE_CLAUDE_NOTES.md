# Azure off switch and optional Claude CLI provider

## Azure for translation
- `TRANSLATION_USE_AZURE` = `1` (default) | `0`/`false` | `auto` (Azure only if `AZURE_TRANSLATOR_KEY` is set). Read at call time.
- Off: `translate_localized_content` never touches Azure (no budget wait, no cooldown, no HTTP); every language goes through the LLM path
  (same concurrent batches, same validation).
- UI: checkbox "Çeviri için Azure kullan" in the LLM provider box. Persisted in `ui_llm_providers.json` (`translation_use_azure`),
  applied via `core.llm_settings.apply_translation_mode(bool)` (toggle + right before `JobManager.start`).
- Knobs: `TRANSLATION_LLM_BATCH_SIZE` (default 8), `TRANSLATION_LLM_WORKERS` (default 3, capped at 6).

## Claude (CLI) provider
- Provider name `claude`; models `sonnet, haiku, opus` (env `CLAUDE_MODELS`); timeout `CLAUDE_TIMEOUT_SECONDS` (60); max 3 concurrent CLI processes.
- Never automatic: used only when `claude` is listed in `LLM_ALLOWED_PROVIDERS` (UI checkbox, default unchecked) AND `claude auth status` says logged in.
  Otherwise the UI shows "Claude (CLI) (giriş yok)" disabled and the manager ignores it.
- Enable: run `claude auth login` once, restart the UI, tick "Claude (CLI)". `CLAUDE_CLI_CMD` overrides the executable path.
- Call: `claude -p --model <m> --tools "" --no-session-persistence --disable-slash-commands`, prompt on stdin, child env without `*_API_KEY`,
  temp cwd, process tree killed on timeout.

## Risks
- Azure off: ~34 languages per question hit the free LLM tiers (Gemini/Groq/Cohere/NVIDIA); expect more 429s/cooldowns, so keep workers at 3-4.
- Claude uses the logged-in account's subscription usage; heavy translation runs can exhaust it. Output is text, JSON is parsed from it (fences stripped).
