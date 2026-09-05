#!/usr/bin/env bash
# Run THIS on the rented CUDA box (not on the Mac). Serves Qwen2.5-Coder-7B-Instruct
# with vLLM + xgrammar on :8000, then keep the Mac pointed at it:
#
#   ssh -L 8000:127.0.0.1:8000 root@<pod-ip> -p <pod-ssh-port>
#   # on the Mac, with scripts/env.sh already sourced:
#   export FSYN_LLM_BASE_URL=http://127.0.0.1:8000/v1
#   export FSYN_LLM_MODEL=Qwen/Qwen2.5-Coder-7B-Instruct
#   export FSYN_LLM_API_KEY=EMPTY
#   fsyn doctor && fsyn baseline && fsyn cegar
#
# Needs ~16 GB VRAM. A single RTX 4090 / A5000 / L4 24GB is enough.
set -euo pipefail

MODEL="${MODEL:-Qwen/Qwen2.5-Coder-7B-Instruct}"
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"

if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "error: nvidia-smi not found; this is not a CUDA box" >&2
  exit 1
fi
nvidia-smi -L

python3 -m pip install -U 'vllm>=0.6' outlines

echo "==> vLLM ${MODEL}  http://${HOST}:${PORT}/v1"
# vLLM 0.8+ dropped --guided-decoding-backend; grammar is requested per-call
# via guided_grammar / structured outputs. xgrammar is still a dependency.
exec python3 -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" \
  --host "$HOST" \
  --port "$PORT" \
  --trust-remote-code \
  --max-model-len "${MAX_MODEL_LEN:-8192}"
