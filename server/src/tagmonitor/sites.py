"""The monitored-site record, as the worker and the alerting code need it."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from tagmonitor.checks.base import SiteConfig
from tagmonitor.queue.jobs import Conn


class Site(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    url: str
    registrable_domain: str
    check_interval_minutes: int
    paused: bool
    alert_email: str
    expected_meta_pixel_ids: list[str]
    expected_ga4_ids: list[str]
    expected_google_ads_ids: list[str]
    next_check_at: datetime

    def check_config(self) -> SiteConfig:
        return SiteConfig(
            url=self.url,
            expected_meta_pixel_ids=self.expected_meta_pixel_ids,
            expected_ga4_ids=self.expected_ga4_ids,
            expected_google_ads_ids=self.expected_google_ads_ids,
        )


async def get_site(conn: Conn, site_id: UUID | str) -> Site | None:
    """Unscoped lookup for internal use (worker, alerts). API queries always scope by user."""
    cursor = await conn.execute(
        """
        SELECT id, user_id, name, url, registrable_domain, check_interval_minutes, paused,
               alert_email, expected_meta_pixel_ids, expected_ga4_ids,
               expected_google_ads_ids, next_check_at
        FROM sites WHERE id = %s
        """,
        (site_id,),
    )
    row = await cursor.fetchone()
    return None if row is None else Site.model_validate(row)
