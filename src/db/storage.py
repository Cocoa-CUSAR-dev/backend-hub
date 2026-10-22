"""Uploaded files and stored geometries.

Owns:
    storage.file
    storage.geo

All SQL for these tables lives in this file and nowhere else (CONVENTIONS.md §2).
Other code calls the functions here.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def insert_geo(
    session: AsyncSession,
    *,
    uploaded_by: UUID,
    wkt: str,
    code: str,
    source_type: str,
    created_at: datetime,
    area_sq_m: float | None = None,
) -> None:
    """Store one shape (WKT, WGS84 lon/lat). `code` is the id of the row it
    belongs to and `source_type` what kind of row ("farm", "plot")."""
    await session.execute(
        text(
            """
            INSERT INTO storage.geo (uploaded_by, geom, code, source_type, area_sq_m, created_at)
            VALUES (:uploaded_by, ST_GeomFromText(:wkt, 4326), :code, :source_type,
                    :area_sq_m, :created_at)
            """
        ),
        {
            "uploaded_by": uploaded_by,
            "wkt": wkt,
            "code": code,
            "source_type": source_type,
            "area_sq_m": area_sq_m,
            "created_at": created_at,
        },
    )


async def insert_file(
    session: AsyncSession,
    *,
    file_name: str,
    table_name: str,
    ref_id: UUID,
    code: str,
    original_name: str,
    file_extension: str,
    mime_type: str,
    file_size: int,
    storage_path: str,
    checksum: str,
    uploaded_by: UUID,
    created_at: datetime,
) -> None:
    """Record one object stored in R2. `table_name` + `ref_id` say which row
    it belongs to, `code` what it is for (e.g. "farm_image")."""
    await session.execute(
        text(
            """
            INSERT INTO storage.file (
                file_name, table_name, ref_id, code, original_name, file_extension,
                mime_type, file_size, storage_path, storage_provider, checksum,
                is_public, status, uploaded_by, created_at
            ) VALUES (
                :file_name, :table_name, :ref_id, :code, :original_name, :file_extension,
                :mime_type, :file_size, :storage_path, 'r2', :checksum,
                FALSE, 'active', :uploaded_by, :created_at
            )
            """
        ),
        {
            "file_name": file_name,
            "table_name": table_name,
            "ref_id": ref_id,
            "code": code,
            "original_name": original_name,
            "file_extension": file_extension,
            "mime_type": mime_type,
            "file_size": file_size,
            "storage_path": storage_path,
            "checksum": checksum,
            "uploaded_by": uploaded_by,
            "created_at": created_at,
        },
    )


async def latest_file_paths(
    session: AsyncSession, *, table_name: str, code: str, ref_ids: list[UUID]
) -> dict[UUID, str]:
    """For each row id, the storage path of its newest active file of this kind."""
    if not ref_ids:
        return {}
    result = await session.execute(
        text(
            """
            SELECT DISTINCT ON (ref_id) ref_id, storage_path
            FROM storage.file
            WHERE table_name = :table_name AND code = :code AND status = 'active'
              AND ref_id = ANY(:ref_ids)
            ORDER BY ref_id, created_at DESC
            """
        ),
        {"table_name": table_name, "code": code, "ref_ids": ref_ids},
    )
    return {row.ref_id: row.storage_path for row in result}
