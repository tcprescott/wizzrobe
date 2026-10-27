#!/usr/bin/env python3
"""Sign in with Wizzrobe from a desktop script, then call the API as that person.

Standard library only, Python 3.9 or later. Register a public app first
(Profile -> Developer apps -> New app -> "An app on a phone or computer, or a
browser-only site") with this redirect URL:

    http://127.0.0.1:8765/callback

Then run:

    python wizzrobe_app_example.py --site https://onsite.speedgaming.org --client-id wza_...

Your browser opens Wizzrobe's consent card. Pick the community and approve, and
the script prints who you are. It also shows a token refresh and a revoke, so
you can see the whole lifecycle. A real app keeps the refresh token somewhere
safe instead of revoking it.
"""

import argparse
import base64
import hashlib
import http.server
import json
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import Dict, Optional, Tuple

REDIRECT_PORT = 8765
REDIRECT_URI = f'http://127.0.0.1:{REDIRECT_PORT}/callback'


def pkce_pair() -> Tuple[str, str]:
    """A fresh PKCE verifier and its S256 challenge (RFC 7636)."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')


def authorize_url(site: str, client_id: str, challenge: str, state: str, scope: str) -> str:
    query = {
        'response_type': 'code',
        'client_id': client_id,
        'redirect_uri': REDIRECT_URI,
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
        'state': state,
    }
    if scope:
        query['scope'] = scope
    return f'{site}/oauth/authorize?{urllib.parse.urlencode(query)}'


def code_exchange_fields(code: str, verifier: str, client_id: str) -> Dict[str, str]:
    return {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI,
        'code_verifier': verifier,
        'client_id': client_id,
    }


def refresh_fields(refresh_token: str, client_id: str) -> Dict[str, str]:
    return {'grant_type': 'refresh_token', 'refresh_token': refresh_token, 'client_id': client_id}


def revoke_fields(token: str, client_id: str) -> Dict[str, str]:
    return {'token': token, 'client_id': client_id}


def wait_for_redirect() -> Dict[str, str]:
    """Serve one request on the loopback redirect and return its query."""
    received: Dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path != '/callback':
                self.send_error(404)
                return
            received.update({k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()})
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'You can close this tab and go back to the terminal.')
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def log_message(self, *args) -> None:
            pass

    with http.server.HTTPServer(('127.0.0.1', REDIRECT_PORT), Handler) as server:
        server.serve_forever()
    return received


def post_form(url: str, fields: Dict[str, str]) -> dict:
    body = urllib.parse.urlencode(fields).encode('ascii')
    request = urllib.request.Request(url, data=body, method='POST', headers={
        'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json',
    })
    try:
        with urllib.request.urlopen(request) as response:
            text = response.read().decode('utf-8')
    except urllib.error.HTTPError as exc:
        sys.exit(f'{url} answered {exc.code}: {exc.read().decode("utf-8", "replace")}')
    return json.loads(text) if text else {}


def get_json(url: str, access_token: str) -> Tuple[int, dict]:
    request = urllib.request.Request(url, headers={
        'Authorization': f'Bearer {access_token}', 'Accept': 'application/json',
    })
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        # 403 with WWW-Authenticate: Bearer error="insufficient_scope" means the
        # person didn't allow what this call needs; 429 comes with Retry-After.
        challenge = exc.headers.get('WWW-Authenticate', '')
        return exc.code, {'detail': exc.read().decode('utf-8', 'replace'), 'challenge': challenge}


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--site', required=True, help='e.g. https://onsite.speedgaming.org')
    parser.add_argument('--client-id', required=True, help='your app\'s client id (starts wza_)')
    parser.add_argument('--scope', default='profile schedule:read',
                        help='space-separated scopes to ask for; empty asks for all the app registered')
    args = parser.parse_args(argv)
    site = args.site.rstrip('/')

    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    url = authorize_url(site, args.client_id, challenge, state, args.scope)
    print(f'Opening your browser. If it does not open, visit:\n  {url}\n')
    webbrowser.open(url)

    reply = wait_for_redirect()
    if reply.get('state') != state:
        sys.exit('The state that came back does not match. Start again.')
    if 'error' in reply:
        sys.exit(f'Not approved: {reply["error"]} {reply.get("error_description", "")}'.strip())
    if reply.get('iss') and reply['iss'] != f'{site}/oauth':
        sys.exit(f'Unexpected issuer {reply["iss"]!r}. Start again.')

    tokens = post_form(f'{site}/oauth/token', code_exchange_fields(reply['code'], verifier, args.client_id))
    print(f'Approved: {tokens["scope"]} (access token lasts {tokens["expires_in"]} s)')

    status, me = get_json(f'{site}/api/users/me', tokens['access_token'])
    if status == 200:
        print(f'Signed in as {me.get("display_name") or me.get("username")}')
    else:
        print(f'/api/users/me answered {status}: {me}')

    # Every refresh hands back a new pair. Keep the new refresh token: sending an
    # old one again ends the whole connection, on the assumption it was stolen.
    tokens = post_form(f'{site}/oauth/token', refresh_fields(tokens['refresh_token'], args.client_id))
    print('Refreshed the token pair.')

    post_form(f'{site}/oauth/revoke', revoke_fields(tokens['refresh_token'], args.client_id))
    print('Disconnected. The app no longer shows under your API tokens.')


if __name__ == '__main__':
    main()
