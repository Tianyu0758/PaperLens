# 文件说明：接收并暂存论文 PDF，校验文件类型、内容头和体积，并计算哈希用于去重。
# 成功后创建论文与入库批次记录；重复文件复用状态，失败时清理临时文件。

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.repositories import PaperRepository


PDF_MIME_TYPE = "application/pdf"


@dataclass(slots=True)
class StagedUpload:
    paper_id: str
    ingestion_id: str | None
    status: str
    pdf_path: Path | None
    duplicate: bool


@dataclass(slots=True)
class UploadService:
    repository: PaperRepository
    upload_path: Path
    max_upload_bytes: int

    async def stage(self, upload: UploadFile) -> StagedUpload:
        filename = upload.filename or ""
        if Path(filename).suffix.casefold() != ".pdf":
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="只接受 .pdf 文件",
            )
        if upload.content_type != PDF_MIME_TYPE:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="文件 MIME 类型必须是 application/pdf",
            )

        self.upload_path.mkdir(parents=True, exist_ok=True)
        temporary_path = self.upload_path / f"upload-{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        total = 0
        header = b""
        try:
            with temporary_path.open("xb") as target:
                while chunk := await upload.read(1024 * 1024):
                    if not header:
                        header = chunk[:5]
                    total += len(chunk)
                    if total > self.max_upload_bytes:
                        raise HTTPException(
                            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                            detail="PDF 文件过大",
                        )
                    digest.update(chunk)
                    target.write(chunk)
            if header != b"%PDF-":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="文件内容不是有效 PDF",
                )
            file_sha256 = digest.hexdigest()
            existing = self.repository.find_by_sha256(file_sha256)
            if existing is not None:
                if existing["status"] == "deleted":
                    paper_id = existing["paper_id"]
                    ingestion_id = str(uuid.uuid4())
                    final_path = self.upload_path / f"{paper_id}.pdf"
                    os.replace(temporary_path, final_path)
                    try:
                        self.repository.restart_deleted(paper_id, ingestion_id)
                    except Exception:
                        final_path.unlink(missing_ok=True)
                        raise
                    return StagedUpload(
                        paper_id=paper_id,
                        ingestion_id=ingestion_id,
                        status="processing",
                        pdf_path=final_path,
                        duplicate=False,
                    )
                temporary_path.unlink(missing_ok=True)
                return StagedUpload(
                    paper_id=existing["paper_id"],
                    ingestion_id=None,
                    status=existing["status"],
                    pdf_path=None,
                    duplicate=True,
                )

            paper_id = str(uuid.uuid4())
            ingestion_id = str(uuid.uuid4())
            final_path = self.upload_path / f"{paper_id}.pdf"
            os.replace(temporary_path, final_path)
            try:
                self.repository.create_processing(paper_id, file_sha256, ingestion_id)
            except Exception:
                final_path.unlink(missing_ok=True)
                raise
            return StagedUpload(
                paper_id=paper_id,
                ingestion_id=ingestion_id,
                status="processing",
                pdf_path=final_path,
                duplicate=False,
            )
        finally:
            temporary_path.unlink(missing_ok=True)
            await upload.close()
