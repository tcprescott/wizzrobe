"""Run the app under cProfile, dumping a profile each time it gets SIGUSR1.

``page_timings.js`` gives a page's server CPU; this says where that CPU goes,
including the part a lazy tab or a websocket refresh spends *after* the page
has loaded, which an in-process ``GET`` never reaches. Every SIGUSR1 writes the
profile gathered since the previous one to ``$PROFILE_DIR/<n>.prof`` and starts
a fresh one, so bracketing a page load with two signals isolates that page:

    set -a; . ./.env; set +a; export MOCK_DISCORD=true ENVIRONMENT=development …
    PROFILE_DIR=/tmp/prof poetry run python scripts/loadtest/profile_server.py &
    kill -USR1 <pid>; <load the page>; kill -USR1 <pid>
    python -c "import pstats; pstats.Stats('/tmp/prof/2.prof').sort_stats('cumulative').print_stats(40)"

Profiling roughly doubles CPU time, so read the proportions, not the totals.
"""

import cProfile
import itertools
import os
import signal

import uvicorn

out_dir = os.environ.get('PROFILE_DIR', '/tmp/wizzrobe-prof')
os.makedirs(out_dir, exist_ok=True)
counter = itertools.count(1)
state = {'prof': cProfile.Profile()}


def _dump(_signum, _frame) -> None:
    prof = state['prof']
    prof.disable()
    path = os.path.join(out_dir, f'{next(counter)}.prof')
    prof.dump_stats(path)
    print(f'profile written: {path}', flush=True)
    state['prof'] = cProfile.Profile()
    state['prof'].enable()


signal.signal(signal.SIGUSR1, _dump)
state['prof'].enable()
uvicorn.run('main:app', app_dir=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), host='127.0.0.1', port=int(os.environ.get('PORT', '8000')), log_level='warning')
