"""Register (or update) Wizzrobe's own app at our OAuth server.

The app in ``web/`` signs in as a first-party public client: no consent
screen, and its grant can act in any community its user belongs to. No page
can mark an app first-party; this script is the only way, run by a platform
admin against the target database.

Usage:
    poetry run python scripts/register_first_party_app.py \\
        --admin-discord-id 123456789 \\
        --redirect https://app.example.org/auth/callback \\
        [--redirect com.example.wizzrobe:/auth/callback] [--client-id wizzrobe-app]

Run it again with the full list of redirect addresses to change them.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main(args: argparse.Namespace) -> None:
    from dotenv import load_dotenv
    from tortoise import Tortoise

    load_dotenv()
    from application.services import get_user_from_discord_id
    from application.services.oauth_app_service import OAuthAppService
    from migrations.tortoise_config import TORTOISE_ORM

    await Tortoise.init(config=TORTOISE_ORM)
    try:
        admin = await get_user_from_discord_id(args.admin_discord_id)
        if admin is None:
            raise SystemExit(f'No user with Discord id {args.admin_discord_id}.')
        app = await OAuthAppService().register_first_party(
            admin, client_id=args.client_id, redirect_uris=args.redirect,
        )
        print(f'First-party app {app.client_id} (id {app.id}) redirects to: {", ".join(app.redirect_uris)}')
    finally:
        await Tortoise.close_connections()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--admin-discord-id', type=int, required=True, help='A platform admin, recorded as the owner.')
    parser.add_argument('--redirect', action='append', required=True, help='A redirect address; repeat for more.')
    parser.add_argument('--client-id', default='wizzrobe-app')
    asyncio.run(main(parser.parse_args()))
