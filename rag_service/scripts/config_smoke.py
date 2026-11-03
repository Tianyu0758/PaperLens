# 文件说明：快速检查 RAG 是否读取到 Qdrant 地址、访问凭据和模型凭据。
# 只输出配置是否存在，不输出密钥值；配置缺失时以失败状态退出。

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings


checks = {
    "qdrant_url": bool(settings.qdrant_url),
    "qdrant_api_key": bool(settings.qdrant_api_key),
    "deepseek_api_key": bool(settings.deepseek_api_key),
}
for name, configured in checks.items():
    print(f"{name}_configured={configured}")
if not all(checks.values()):
    raise SystemExit(1)
print("CONFIG_SMOKE_OK")
