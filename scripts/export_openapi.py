"""Write the REST API's OpenAPI schema to a file, without booting the app.

The new app in ``web/`` generates its typed client from this file
(``npm run gen:api``), and CI fails when the committed copy drifts from the
routers. Builds the same document ``/api/openapi.json`` serves, minus the
server's base URL.

Usage: poetry run python scripts/export_openapi.py [web/openapi.json]
"""

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api
from api.openapi_docs import api_description, install

_FROZEN_TIME = '2026-01-01T00:00:00+00:00'


def _freeze_clock(node: Any) -> Any:
    """The webhook examples stamp ``occurred_at`` with now; pin it so the file is stable."""
    if isinstance(node, dict):
        return {k: _FROZEN_TIME if k == 'occurred_at' and isinstance(v, str) else _freeze_clock(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_freeze_clock(v) for v in node]
    return node


def build() -> dict:
    app = FastAPI(title='Wizzrobe API', description=api_description(), version='1.0.0', openapi_url='/api/openapi.json')
    app.include_router(api.router, prefix='/api')
    install(app)
    return _freeze_clock(app.openapi())


def main() -> None:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else 'web/openapi.json')
    target.write_text(json.dumps(build(), indent=2, sort_keys=True, ensure_ascii=False) + '\n')
    print(f'Wrote {target}')


if __name__ == '__main__':
    main()
