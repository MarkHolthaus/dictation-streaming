#!/bin/zsh
# Start the dictation engine. Needs a normal (unsandboxed) shell for the GPU.
cd "$(dirname "$0")"
# Models are cached under .models/hf. The first run downloads them, so the hub is online by
# default. After that, start with HF_HUB_OFFLINE=1 to never touch the network.
export HF_HOME="$PWD/.models/hf" HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-0}"
# Speech model weights: 8-bit (default). Start with DICTATION_ASR_BITS=4 for 4-bit, 0 for bf16.
export DICTATION_ASR_BITS="${DICTATION_ASR_BITS:-8}"
exec .venv/bin/python -m engine.server
