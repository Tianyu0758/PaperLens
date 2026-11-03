# 文件说明：封装 Qdrant 中稠密向量和文本块元数据的存储与搜索操作。
# 入库时先写入暂存且不可见的数据，再激活对应批次；查询仅检索可见数据并支持范围过滤。

from __future__ import annotations

from qdrant_client import AsyncQdrantClient, models

from app.config import Settings


PAYLOAD_INDEXES: tuple[tuple[str, models.PayloadSchemaType], ...] = (
    ("chunk_id", models.PayloadSchemaType.KEYWORD),
    ("paper_id", models.PayloadSchemaType.KEYWORD),
    ("ingestion_id", models.PayloadSchemaType.KEYWORD),
    ("section_type", models.PayloadSchemaType.KEYWORD),
    ("visible", models.PayloadSchemaType.BOOL),
)


async def create_qdrant_client(settings: Settings) -> AsyncQdrantClient:
    settings.validate_qdrant()
    return AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
        timeout=20,
    )


async def ensure_collection(
    client: AsyncQdrantClient, collection_name: str
) -> None:
    if not await client.collection_exists(collection_name):
        await client.create_collection(
            collection_name=collection_name,
            vectors_config={
                "dense": models.VectorParams(
                    size=1024,
                    distance=models.Distance.COSINE,
                )
            },
        )
    for field_name, field_schema in PAYLOAD_INDEXES:
        await client.create_payload_index(
            collection_name=collection_name,
            field_name=field_name,
            field_schema=field_schema,
            wait=True,
        )


class QdrantChunkStore:
    def __init__(
        self,
        client: AsyncQdrantClient,
        collection_name: str,
        batch_size: int = 64,
    ) -> None:
        self.client = client
        self.collection_name = collection_name
        self.batch_size = batch_size

    @staticmethod
    def _ingestion_filter(ingestion_id: str) -> models.Filter:
        return models.Filter(
            must=[
                models.FieldCondition(
                    key="ingestion_id",
                    match=models.MatchValue(value=ingestion_id),
                )
            ]
        )

    async def upsert_staging(
        self,
        paper_id: str,
        ingestion_id: str,
        chunks: list[dict],
        vectors: list[list[float]],
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunk and vector counts differ")
        for start in range(0, len(chunks), self.batch_size):
            points = []
            for chunk, vector in zip(
                chunks[start : start + self.batch_size],
                vectors[start : start + self.batch_size],
                strict=True,
            ):
                payload = {
                    "chunk_id": chunk["chunk_id"],
                    "paper_id": paper_id,
                    "ingestion_id": ingestion_id,
                    "visible": False,
                    "section_path": chunk["section_path"],
                    "section_type": chunk["section_type"],
                    "page_number": chunk.get("page_number"),
                    "chunk_index": chunk["chunk_index"],
                    "text": chunk["text"],
                }
                points.append(
                    models.PointStruct(
                        id=chunk["chunk_id"],
                        vector={"dense": vector},
                        payload=payload,
                    )
                )
            await self.client.upsert(
                collection_name=self.collection_name,
                points=points,
                wait=True,
            )

    async def count_ingestion(self, ingestion_id: str) -> int:
        result = await self.client.count(
            collection_name=self.collection_name,
            count_filter=self._ingestion_filter(ingestion_id),
            exact=True,
        )
        return result.count

    async def delete_ingestion(self, ingestion_id: str) -> None:
        await self.client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(
                filter=self._ingestion_filter(ingestion_id)
            ),
            wait=True,
        )

    async def delete_paper(self, paper_id: str) -> None:
        await self.client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="paper_id",
                            match=models.MatchValue(value=paper_id),
                        )
                    ]
                )
            ),
            wait=True,
        )

    async def activate_ingestion(self, ingestion_id: str) -> None:
        await self.client.set_payload(
            collection_name=self.collection_name,
            payload={"visible": True},
            points=self._ingestion_filter(ingestion_id),
            wait=True,
        )

    async def dense_search(
        self,
        vector: list[float],
        *,
        paper_id: str | None = None,
        section_type: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        conditions = [
            models.FieldCondition(
                key="visible",
                match=models.MatchValue(value=True),
            )
        ]
        if paper_id is not None:
            conditions.append(
                models.FieldCondition(
                    key="paper_id", match=models.MatchValue(value=paper_id)
                )
            )
        if section_type is not None:
            conditions.append(
                models.FieldCondition(
                    key="section_type",
                    match=models.MatchValue(value=section_type),
                )
            )
        response = await self.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            using="dense",
            query_filter=models.Filter(must=conditions),
            limit=limit,
            with_payload=True,
            with_vectors=["dense"],
        )
        results = []
        for point in response.points:
            item = dict(point.payload or {})
            item["chunk_id"] = item.get("chunk_id", str(point.id))
            item["dense_score"] = float(point.score)
            vectors = point.vector or {}
            item["embedding"] = vectors.get("dense") if isinstance(vectors, dict) else vectors
            results.append(item)
        return results

    async def fetch_vectors(self, chunk_ids: list[str]) -> dict[str, list[float]]:
        if not chunk_ids:
            return {}
        points = await self.client.retrieve(
            collection_name=self.collection_name,
            ids=chunk_ids,
            with_payload=["chunk_id"],
            with_vectors=["dense"],
        )
        vectors: dict[str, list[float]] = {}
        for point in points:
            payload = point.payload or {}
            chunk_id = payload.get("chunk_id", str(point.id))
            vector = point.vector or {}
            dense = vector.get("dense") if isinstance(vector, dict) else vector
            if dense is not None:
                vectors[chunk_id] = dense
        return vectors
