# 文件说明：验证 PDF 类型、内容头、体积、哈希去重及删除后重新上传的行为。
# 使用临时文件和数据库，检查无效或失败上传不会遗留不完整文件。

from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.db import Database
from app.repositories import PaperRepository
from app.upload_service import UploadService


def make_upload(
    content: bytes, filename: str = "paper.pdf", content_type: str = "application/pdf"
) -> UploadFile:
    return UploadFile(
        file=BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


def make_service(tmp_path: Path) -> tuple[UploadService, PaperRepository]:
    database = Database(tmp_path / "papers.db")
    database.initialize()
    repository = PaperRepository(database)
    return UploadService(repository, tmp_path / "uploads", 1024), repository


@pytest.mark.asyncio
async def test_stage_pdf_and_deduplicate_by_sha256(tmp_path: Path) -> None:
    service, repository = make_service(tmp_path)
    content = b"%PDF-1.7\nminimal smoke payload"

    first = await service.stage(make_upload(content))
    second = await service.stage(make_upload(content))

    assert first.status == "processing"
    assert first.duplicate is False
    assert first.pdf_path is not None and first.pdf_path.exists()
    assert repository.get(first.paper_id)["status"] == "processing"
    assert second.duplicate is True
    assert second.paper_id == first.paper_id
    assert len(list((tmp_path / "uploads").glob("*.pdf"))) == 1
    assert not list((tmp_path / "uploads").glob("*.part"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "content_type", "content", "expected_status"),
    [
        ("paper.txt", "application/pdf", b"%PDF-1.7", 415),
        ("paper.pdf", "text/plain", b"%PDF-1.7", 415),
        ("paper.pdf", "application/pdf", b"not a pdf", 422),
    ],
)
async def test_rejects_invalid_uploads(
    tmp_path: Path,
    filename: str,
    content_type: str,
    content: bytes,
    expected_status: int,
) -> None:
    service, _ = make_service(tmp_path)
    with pytest.raises(HTTPException) as error:
        await service.stage(make_upload(content, filename, content_type))
    assert error.value.status_code == expected_status
    assert not list((tmp_path / "uploads").glob("*"))


@pytest.mark.asyncio
async def test_rejects_oversized_upload_and_removes_partial_file(tmp_path: Path) -> None:
    service, _ = make_service(tmp_path)
    with pytest.raises(HTTPException) as error:
        await service.stage(make_upload(b"%PDF-" + b"x" * 2048))
    assert error.value.status_code == 413
    assert not list((tmp_path / "uploads").glob("*"))


@pytest.mark.asyncio
async def test_reupload_deleted_pdf_starts_new_ingestion(tmp_path: Path) -> None:
    service, repository = make_service(tmp_path)
    content = b"%PDF-1.7\nreupload payload"
    first = await service.stage(make_upload(content))
    repository.mark_ready(first.paper_id, first.ingestion_id)
    repository.begin_deleting(first.paper_id)
    repository.finish_deleting(first.paper_id)
    first.pdf_path.unlink()

    second = await service.stage(make_upload(content))

    assert second.paper_id == first.paper_id
    assert second.ingestion_id != first.ingestion_id
    assert second.duplicate is False
    assert second.status == "processing"
    assert repository.get(first.paper_id)["status"] == "processing"
    assert second.pdf_path is not None and second.pdf_path.exists()
