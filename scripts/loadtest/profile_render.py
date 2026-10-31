"""Profile a single tenant-home page render in-process (no new deps).

Boots main:app through its real lifespan, warms one render, then cProfiles a
batch of renders so the hot path of the ~250ms/render CPU cost is visible.
"""
import asyncio
import cProfile
import io
import os
import pstats

import httpx

COOKIE = open(os.environ.get('COOKIE_FILE', 'cookie.txt')).read().strip()
PATH = '/t/default/'
N = 10


async def main() -> None:
    import main as appmod
    app = appmod.app
    transport = httpx.ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url='http://test', timeout=60) as c:
            r = await c.get(PATH, headers={'Cookie': COOKIE})
            print(f'warmup: {r.status_code}, {len(r.text)} bytes')
            if r.status_code != 200:
                print(r.text[:500])
                return

            pr = cProfile.Profile()
            pr.enable()
            for _ in range(N):
                await c.get(PATH, headers={'Cookie': COOKIE})
            pr.disable()

            s = io.StringIO()
            ps = pstats.Stats(pr, stream=s).sort_stats('cumulative')
            ps.print_stats(45)
            out = s.getvalue()
            print('\n'.join(out.splitlines()[:70]))

            s2 = io.StringIO()
            pstats.Stats(pr, stream=s2).sort_stats('tottime').print_stats(25)
            print('\n===== BY SELF TIME (tottime) =====')
            print('\n'.join(s2.getvalue().splitlines()[4:40]))


asyncio.run(main())
