#!/usr/bin/env bash
# Starts a small AI model on this machine with Ollama (https://ollama.com).
# No account and no key are needed. The Ollama program is one fixed version,
# and its checksum is checked before it is used.
set -euo pipefail

VERSION="v0.40.16"
SHA256="87bfafb2acbc89b2e6a1be0a9043de6aa75fb450696f86bc426e65fc448f788d"
CHAT_MODEL="phi4-mini"
EMBED_MODEL="all-minilm"

DIR="$RUNNER_TEMP/ollama"
mkdir -p "$DIR"
cd "$DIR"

echo "Downloading Ollama $VERSION ..."
curl -fsSL --retry 3 -o ollama.tar.zst "https://github.com/ollama/ollama/releases/download/$VERSION/ollama-linux-amd64.tar.zst"
echo "$SHA256  ollama.tar.zst" | sha256sum -c -
tar --zstd -xf ollama.tar.zst --exclude='lib/ollama/cuda*' --exclude='lib/ollama/vulkan*' --exclude='lib/ollama/mlx*'
rm -f ollama.tar.zst

BIN="$DIR/bin/ollama"
if [ ! -x "$BIN" ]; then
  BIN="$(find "$DIR" -maxdepth 3 -type f -name ollama | head -n 1)"
fi
echo "Ollama program: $BIN"

export OLLAMA_HOST="127.0.0.1:11434"
export OLLAMA_MODELS="$DIR/models"
nohup "$BIN" serve > "$DIR/server.log" 2>&1 &

for attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:11434/api/version > /dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:11434/api/version
echo

"$BIN" pull "$EMBED_MODEL"
"$BIN" pull "$CHAT_MODEL"
"$BIN" list
