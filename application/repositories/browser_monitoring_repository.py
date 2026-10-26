"""Browser Monitoring Repository — data access for browser-side Sentry settings.

``BrowserMonitoringSettings`` (a singleton) and ``BrowserErrorIgnoreRule`` are
**global** (no tenant column): the browser SDK reports to the deployment's one
Sentry project and is managed on ``/platform``. Nothing here is tenant-scoped.
"""

from typing import List, Optional

from models import BrowserErrorIgnoreRule, BrowserMonitoringSettings, User

_SETTINGS_ID = 1


class BrowserMonitoringRepository:
    """Unscoped access to the settings row and the ignore rules."""

    @staticmethod
    async def get_settings() -> Optional[BrowserMonitoringSettings]:
        return await BrowserMonitoringSettings.get_or_none(id=_SETTINGS_ID).prefetch_related('updated_by')

    @staticmethod
    async def save_settings(
        enabled: bool, replay_on_error: bool, sample_rate: float, updated_by: Optional[User],
    ) -> BrowserMonitoringSettings:
        settings, _ = await BrowserMonitoringSettings.update_or_create(
            id=_SETTINGS_ID,
            defaults={
                'enabled': enabled, 'replay_on_error': replay_on_error,
                'sample_rate': sample_rate, 'updated_by': updated_by,
            },
        )
        return settings

    @staticmethod
    async def list_rules() -> List[BrowserErrorIgnoreRule]:
        return await BrowserErrorIgnoreRule.all().prefetch_related('created_by').order_by('created_at', 'id')

    @staticmethod
    async def get_rule(rule_id: int) -> Optional[BrowserErrorIgnoreRule]:
        return await BrowserErrorIgnoreRule.get_or_none(id=rule_id)

    @staticmethod
    async def find_rule(pattern: str, match: str) -> Optional[BrowserErrorIgnoreRule]:
        return await BrowserErrorIgnoreRule.get_or_none(pattern=pattern, match=match)

    @staticmethod
    async def create_rule(pattern: str, match: str, created_by: Optional[User]) -> BrowserErrorIgnoreRule:
        return await BrowserErrorIgnoreRule.create(pattern=pattern, match=match, created_by=created_by)

    @staticmethod
    async def delete_rule(rule: BrowserErrorIgnoreRule) -> None:
        await rule.delete()
