import math
import time

from qdrant_client import AsyncQdrantClient, models

from contracts.cache import CacheEntry
from gateway.cache_rules import CacheContext
from gateway.cache_store import expires_at


VECTOR_SIZE = 384


def normalize_vector(vector: list[float]) -> list[float]:
    if len(vector) != VECTOR_SIZE:
        raise ValueError(f"Expected {VECTOR_SIZE}-dimensional embedding")

    if not all(math.isfinite(value) for value in vector):
        raise ValueError("Embedding contains non-finite values")

    norm = math.sqrt(sum(value * value for value in vector))

    if norm == 0:
        raise ValueError("Embedding cannot be a zero vector")

    return [value / norm for value in vector]


class VectorStore:
    def __init__(self, url: str, collection: str):
        self.client = AsyncQdrantClient(url=url, timeout=5)
        self.collection = collection

    async def initialize(self) -> None:
        if not await self.client.collection_exists(self.collection):
            await self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=VECTOR_SIZE,
                    distance=models.Distance.COSINE,
                ),
            )

        info = await self.client.get_collection(self.collection)
        config = info.config.params.vectors

        if (
            not isinstance(config, models.VectorParams)
            or config.size != VECTOR_SIZE
            or config.distance != models.Distance.COSINE
        ):
            raise RuntimeError(
                "Qdrant collection must use one 384-dimensional cosine vector"
            )

        await self.client.create_payload_index(
            collection_name=self.collection,
            field_name="compatibility_key",
            field_schema=models.PayloadSchemaType.KEYWORD,
            wait=True,
        )

        await self.client.create_payload_index(
            collection_name=self.collection,
            field_name="expires_at",
            field_schema=models.PayloadSchemaType.FLOAT,
            wait=True,
        )

    async def store(
        self,
        context: CacheContext,
        entry: CacheEntry,
    ) -> None:
        await self.client.upsert(
            collection_name=self.collection,
            points=[
                models.PointStruct(
                    id=entry.entry_id,
                    vector=normalize_vector(entry.embedding),
                    payload={
                        "compatibility_key": context.compatibility_key,
                        "expires_at": expires_at(entry),
                        "entry": entry.model_dump(mode="json"),
                    },
                )
            ],
            wait=True,
        )

    async def search(
        self,
        context: CacheContext,
        embedding: list[float],
    ) -> list[CacheEntry]:
        result = await self.client.query_points(
            collection_name=self.collection,
            query=normalize_vector(embedding),
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="compatibility_key",
                        match=models.MatchValue(
                            value=context.compatibility_key,
                        ),
                    ),
                    models.FieldCondition(
                        key="expires_at",
                        range=models.Range(gt=time.time()),
                    ),
                ]
            ),
            limit=5,
            with_payload=True,
            with_vectors=False,
        )

        entries = []

        for point in result.points:
            payload = point.payload or {}

            # Defensive checks in addition to the database filter.
            if payload.get("compatibility_key") != context.compatibility_key:
                continue

            if payload.get("expires_at", 0) <= time.time():
                continue

            entry = CacheEntry.model_validate(payload["entry"])

            if expires_at(entry) <= time.time():
                continue

            entry.embedding = normalize_vector(entry.embedding)
            entries.append(entry)

        return entries

    async def close(self) -> None:
        await self.client.close()