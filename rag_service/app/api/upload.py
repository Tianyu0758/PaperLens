# 文件说明：接收 PDF 上传请求，调用上传服务完成校验与暂存，并安排后台入库。
# 接口先返回 paper_id 和状态，客户端随后查询状态，等待论文解析与索引就绪。

from fastapi import APIRouter, BackgroundTasks, File, Request, UploadFile

from app.schemas import UploadResponse


router = APIRouter(tags=["upload"])


@router.post("/upload", response_model=UploadResponse)
async def upload_pdf(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> UploadResponse:
    staged = await request.app.state.upload_service.stage(file)
    if not staged.duplicate:
        background_tasks.add_task(
            request.app.state.ingestion_processor.process,
            staged.paper_id,
            staged.ingestion_id,
            staged.pdf_path,
        )
    return UploadResponse(paper_id=staged.paper_id, status=staged.status)
