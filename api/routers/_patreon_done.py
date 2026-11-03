"""The page a Patreon sign-in lands on.

Both sign-ins start in the app and open Patreon in a browser tab. Neither the
app's screens nor the website live at a fixed path that tab can return to, so
the callback ends the tab here and the app picks up the change when its
window comes back into focus.
"""

from html import escape

_PAGES: dict[str, tuple[str, str]] = {
    'connected': (
        'Patreon is connected',
        'Members who link Patreon get their badge within a few minutes.',
    ),
    'linked': (
        'Patreon is linked',
        'If you back this community on Patreon, your badge shows within a few minutes.',
    ),
    'taken': (
        "That Patreon account is already linked",
        "It's linked to another Wizzrobe account. Unlink it there first, or sign in to Patreon as someone else.",
    ),
    'cancelled': (
        'Nothing changed',
        'You cancelled on Patreon, so nothing was linked.',
    ),
    'expired': (
        'That sign-in has expired',
        'A sign-in lasts ten minutes and works once. Start again from Wizzrobe.',
    ),
    'failed': (
        "Patreon sign-in didn't finish",
        "Start again from Wizzrobe. If it keeps failing, tell your community's staff.",
    ),
}


def done_page(outcome: str, *, creator: bool = False) -> str:
    title, body = _PAGES.get(outcome, _PAGES['failed'])
    if outcome == 'failed' and creator:
        body = 'Start again from Settings → Integrations in a minute.'
    ok = outcome in ('connected', 'linked')
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{escape(title)} · Wizzrobe</title>
<style>
:root {{ color-scheme: light dark; --bg: #f5f4f1; --card: #ffffff; --fg: #1c1b19; --muted: #57534e; --line: #e2dfd8; --mark: {'#2f6b2f' if ok else '#8a4b0b'}; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #161513; --card: #201f1c; --fg: #f2f0eb; --muted: #c2bdb4; --line: #34322e; --mark: {'#8fd18f' if ok else '#f0b46a'}; }} }}
body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: var(--bg); color: var(--fg);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; padding: 16px; box-sizing: border-box; }}
main {{ max-width: 420px; width: 100%; background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 24px; }}
h1 {{ font-size: 1.25rem; margin: 0 0 8px; }}
h1::before {{ content: "{'✓' if ok else '!'}"; color: var(--mark); margin-right: 8px; }}
p {{ margin: 0 0 12px; color: var(--muted); }}
p:last-child {{ margin: 0; }}
</style>
</head>
<body>
<main>
<h1>{escape(title)}</h1>
<p>{escape(body)}</p>
<p>You can close this tab and go back to Wizzrobe.</p>
</main>
</body>
</html>"""
