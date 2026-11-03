"""Which build is running.

Unauthenticated like ``/health``: the same version and commit are printed on the
public About page, and a deploy check or bug report needs them without a token.
"""

from fastapi import APIRouter

from api.schemas.version import VersionResponse
from application.utils.build_info import build_info

router = APIRouter(tags=['health'])


@router.get(
    '/version',
    response_model=VersionResponse,
    summary='Get the running version',
    description=(
        "Reports which build of Wizzrobe is answering: its version, the git commit it was built "
        "from, that commit's date, and a link to the source at that commit. Use it to check "
        "whether a fix has been deployed.\n\n"
        "**Who can call this:** Anyone; no token needed."
    ),
)
async def version() -> VersionResponse:
    info = build_info()
    return VersionResponse(
        version=info.version,
        commit=info.commit,
        commit_short=info.commit_short,
        commit_date=info.commit_date,
        source_url=info.source_url,
    )
