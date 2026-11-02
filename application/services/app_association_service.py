"""
App Association Service - the files that let a link open the store app.

iOS reads ``/.well-known/apple-app-site-association`` and Android reads
``/.well-known/assetlinks.json`` from the site's own domain to confirm the app
may open its links (universal links / app links). Both are built from settings
and answer nothing until those are set:

- ``APP_ID``: the iOS bundle id and Android package name (one id for both).
- ``APPLE_TEAM_ID``: the Apple developer team that signs the iOS app.
- ``ANDROID_CERT_SHA256``: the Android signing certificate fingerprints,
  comma separated (Play's app-signing key, plus an upload or debug key).

Only community links (``/t/…``) open the app; everything else on the domain
(sign-in, the API, help pages) stays in the browser.
"""

import os
import re
from typing import Dict, List, Optional

APP_LINK_PATHS = ['/t/*']

_TEAM_ID = re.compile(r'^[A-Z0-9]{10}$')
_APP_ID = re.compile(r'^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$')
_FINGERPRINT = re.compile(r'^([0-9A-F]{2}:){31}[0-9A-F]{2}$')


def _env(name: str) -> str:
    return (os.environ.get(name) or '').strip()


class AppAssociationService:
    @staticmethod
    def app_id() -> Optional[str]:
        value = _env('APP_ID')
        return value if _APP_ID.match(value) else None

    @staticmethod
    def apple_association() -> Optional[Dict]:
        """The apple-app-site-association document, or None when iOS isn't set up."""
        app_id, team = AppAssociationService.app_id(), _env('APPLE_TEAM_ID').upper()
        if not app_id or not _TEAM_ID.match(team):
            return None
        return {
            'applinks': {
                'details': [
                    {
                        'appIDs': [f'{team}.{app_id}'],
                        'components': [{'/': path} for path in APP_LINK_PATHS],
                    }
                ]
            },
        }

    @staticmethod
    def android_fingerprints() -> List[str]:
        raw = [f.strip().upper() for f in _env('ANDROID_CERT_SHA256').split(',')]
        return [f for f in raw if _FINGERPRINT.match(f)]

    @staticmethod
    def android_asset_links() -> Optional[List[Dict]]:
        """The assetlinks.json statements, or None when Android isn't set up."""
        app_id, prints = AppAssociationService.app_id(), AppAssociationService.android_fingerprints()
        if not app_id or not prints:
            return None
        return [
            {
                'relation': ['delegate_permission/common.handle_all_urls'],
                'target': {'namespace': 'android_app', 'package_name': app_id, 'sha256_cert_fingerprints': prints},
            }
        ]
