"""The user's alert history (PRD §17)."""

from typing import Annotated

from fastapi import APIRouter, Query

from tagmonitor.api.deps import PoolDep, UserDep
from tagmonitor.api.schemas import AlertOut

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.get("")
async def list_alerts(
    user: UserDep, pool: PoolDep, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[AlertOut]:
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                """
                SELECT a.id, a.site_id, s.name AS site_name, a.check_key, a.kind, a.subject,
                       a.created_at, a.sent_at
                FROM alerts a JOIN sites s ON s.id = a.site_id
                WHERE s.user_id = %s
                ORDER BY a.created_at DESC, a.id DESC
                LIMIT %s
                """,
                (user.id, limit),
            )
        ).fetchall()
    return [AlertOut(**row) for row in rows]
