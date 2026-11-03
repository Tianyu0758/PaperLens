#!/usr/bin/env bash
# 文件说明：使用独立 GPU 环境启动 Embedding，模型、服务名、端口和显存参数来自 rag_service/.env。
# 环境默认位于 rag_service/.venv-models；MODEL_PYTHON 可指定其他已安装模型依赖的 Python。
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${MODEL_PYTHON:-$PROJECT_DIR/.venv-models/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "GPU Python not found. Create rag_service/.venv-models or set MODEL_PYTHON." >&2
  exit 1
fi
exec "$PYTHON_BIN" -B "$PROJECT_DIR/../scripts/manage.py" embedding "$@"
