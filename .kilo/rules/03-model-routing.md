# Kilo Model Routing (Part 2)

Route by task cost. Do not send RTL to a public API if the user has asked for air-gapped work — use the local vLLM endpoint instead.

## Scaffolding and boilerplate
Verilog DUTs, Python wrappers, `.sby` files, harness edits.

- Preferred: `deepseek/deepseek-chat` (DeepSeek-V3) via OpenRouter
- Alternate: `qwen/qwen-2.5-coder-32b-instruct` via OpenRouter

## Temporal logic and CEGAR repair
`|->` vs `|=>`, `$past` / `$rose`, vacuity, SMT counterexample repair.

- Preferred: `deepseek/deepseek-r1` via OpenRouter (reasoning tokens before the edit)

## Local zero-cost fallback
When `http://localhost:8000/v1` is up (see `scripts/run_vllm.sh`):

- Model: `Qwen/Qwen2.5-Coder-7B-Instruct`
- Base URL: `http://localhost:8000/v1`
- API key: any non-empty string (vLLM ignores it)

Configure the key and default model in the Kilo UI (OpenRouter BYOK, or a custom OpenAI-compatible provider). Never commit API keys.
