# 文件说明：用浏览器检查 examples 中的离线演示，并保存逐页截图和规则报告。
# 直接运行只检查本地示例；发现问题或浏览器不可用时以失败状态退出。

import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from app.presentation_inspector import inspect_html

root=Path(__file__).resolve().parents[1]
report=asyncio.run(inspect_html(root/"examples/presentation.html",root/"data/verification/example"))
(root/"data/verification/example/report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"status":report["status"],"issues":report["issues"],"pages":len(report["pages"])},ensure_ascii=True))
raise SystemExit(0 if report["passed"] else 1)
