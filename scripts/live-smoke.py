# 文件说明：显式执行真实外部链路验证：上传 PDF、读取章节、生成 HTML，再连续修改指定页面。
# 会调用真实模型并写入新项目的数据；检查父版本关系与未修改内容，结果保存在验证目录。

"""Explicit real-service check: uploads to the new RAG and uses paid model calls."""
import argparse
import json
import time
import uuid
from pathlib import Path

import httpx

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument("--pdf",required=True)
parser.add_argument("--agent",default="http://127.0.0.1:18091")
parser.add_argument("--user",default="paperlens-live-"+uuid.uuid4().hex[:8])
args=parser.parse_args()
client=httpx.Client(base_url=args.agent,timeout=60,trust_env=False)
report={"user_id":args.user,"runs":[]}
destination=root/"data/verification/live-smoke.json"
destination.parent.mkdir(parents=True,exist_ok=True)

def save(): destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
def request(method,url,**kwargs):
    response=client.request(method,url,**kwargs)
    response.raise_for_status()
    return response.json()
def run(question,request_id):
    job=request("POST",f"/sessions/{report['session_id']}/runs",params={"user_id":args.user},
                json={"question":question,"request_id":request_id,"mode":"agent"})
    rid=job["run_id"]
    deadline=time.monotonic()+950
    previous=None
    while time.monotonic()<deadline:
        value=request("GET",f"/runs/{rid}",params={"user_id":args.user})
        state=(value["status"],value["stage"],value["last_event_seq"])
        if state!=previous:
            print(json.dumps({"run_id":rid,"status":state[0],"stage":state[1],"seq":state[2]}),flush=True)
            previous=state
        if value["status"] in ("completed","failed","interrupted"):
            report["runs"].append(value);save()
            if value["status"]!="completed": raise RuntimeError(value.get("error"))
            return value
        time.sleep(2)
    raise TimeoutError("Live run did not complete")
def artifact_body(item):
    return request("GET",f"/artifacts/{item['artifact_id']}",params={"user_id":args.user})

try:
    with Path(args.pdf).open("rb") as handle:
        uploaded=request("POST","/papers/upload",files={"file":(Path(args.pdf).name,handle,"application/pdf")})
    report["paper_id"]=uploaded["paper_id"];save()
    for _ in range(120):
        status=request("GET",f"/papers/{uploaded['paper_id']}/status")
        if status["status"]=="ready": break
        if status["status"]=="failed": raise RuntimeError(status.get("error_message"))
        time.sleep(2)
    else: raise TimeoutError("Ingestion timed out")
    overview=request("GET",f"/papers/{uploaded['paper_id']}/inspect")
    assert overview["sections"]
    excerpt=request("GET",f"/papers/{uploaded['paper_id']}/read",params={"section_id":overview["sections"][0]["section_id"],"limit":2000})
    assert excerpt["results"][0]["text"]
    report["inspection"]={"section_count":len(overview["sections"]),"source":overview["source"]}
    session=request("POST","/sessions",json={"user_id":args.user,"paper_id":uploaded["paper_id"],"title":"HTML 真实论文联调"})
    report["session_id"]=session["session_id"];save()
    first=run("请根据论文证据直接制作一份5分钟的中文组会 HTML 演示，总共4至6页，包含一个简洁的方法流程图、实验发现和局限性。缺少可靠数字时用要点页，不编造图表数值。每页简短，完成后检查页面与交互。", "live-create-1")
    files=request("GET",f"/sessions/{session['session_id']}/artifacts",params={"user_id":args.user})["artifacts"]
    plans=[f for f in files if f["filename"].endswith(".slides.json")]
    source=plans[0]; old=artifact_body(source)
    target=next((s for s in old["slides"] if s["template"]=="flowchart"),old["slides"][1])
    revised=run(f"仅将演示稿 {source['artifact_id']} 中页面 ID {target['slide_id']} 的标题改为“方法流程与关键步骤”。保持其他页面内容、顺序、听众和时长完全不变，不需要补充研究。请生成新版 HTML 并检查。", "live-revise-1")
    files=request("GET",f"/sessions/{session['session_id']}/artifacts",params={"user_id":args.user})["artifacts"]
    new_plan=next(f for f in files if f["filename"].endswith(".slides.json") and f["run_id"]==revised["run_id"])
    new=artifact_body(new_plan)
    assert new["parent_artifact_id"]==source["artifact_id"]
    assert new["duration_minutes"]==old["duration_minutes"]
    assert len(new["slides"])==len(old["slides"])
    for before,after in zip(old["slides"],new["slides"]):
        if before["slide_id"]!=target["slide_id"]: assert before==after
        else: assert {k:v for k,v in before.items() if k!="title"}=={k:v for k,v in after.items() if k!="title"}
    assert any(f["filename"].endswith(".html") and f["run_id"]==revised["run_id"] for f in files)
    report["ok"]=True
    report["partial_revision_preserved_other_slides"]=True
    save()
    print(json.dumps({"ok":True,"session_id":report["session_id"],"runs":len(report["runs"])}),flush=True)
except Exception as exc:
    report["ok"]=False;report["error"]=str(exc);save();raise
