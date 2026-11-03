#!/usr/bin/env bash
# 文件说明：在 Linux/WSL 中启动 RAG API，默认使用 rag_service/.venv。
# 外部服务由 compose 和模型启动入口分别管理；前台运行，Ctrl+C 停止，不接管其他服务。
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PAPERLENS_RAG_PYTHON:-$PROJECT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "RAG Python not found. Create rag_service/.venv or set PAPERLENS_RAG_PYTHON." >&2
  exit 1
fi
action="${1:-start}"
if [[ $# -gt 0 ]]; then shift; fi
case "$action" in
  start) exec "$PYTHON_BIN" -B "$PROJECT_DIR/../scripts/manage.py" rag "$@" ;;
  status) exec "$PYTHON_BIN" -B "$PROJECT_DIR/../scripts/manage.py" doctor --services ;;
  *) echo "Usage: $0 {start|status} [--port PORT]. Stop a foreground API with Ctrl+C." >&2; exit 2 ;;
esac
