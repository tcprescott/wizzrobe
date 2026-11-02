"""``/.well-known`` files that let community links open the store apps.

Served at the site root, not under ``/api``: iOS and Android fetch them from
fixed paths on the domain. Both answer 404 until the app's ids are set (see
``AppAssociationService``).
"""

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse, Response

from application.services import AppAssociationService

router = APIRouter(include_in_schema=False)

_CACHE = {'Cache-Control': 'public, max-age=3600'}


@router.get('/.well-known/apple-app-site-association')
async def apple_app_site_association() -> Response:
    document = AppAssociationService.apple_association()
    if document is None:
        return Response(status_code=404)
    return JSONResponse(document, headers=_CACHE)


@router.get('/.well-known/assetlinks.json')
async def asset_links() -> Response:
    statements = AppAssociationService.android_asset_links()
    if statements is None:
        return Response(status_code=404)
    return JSONResponse(statements, headers=_CACHE)


def mount(app: FastAPI) -> None:
    app.include_router(router)
