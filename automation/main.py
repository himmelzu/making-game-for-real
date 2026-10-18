#!/usr/bin/env python3
"""Launch the local experimental Windows FM26 helper. No external HTTP service."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import webbrowser

from fm26auto.backend import Backend, choose_folder


class Server(ThreadingHTTPServer):
    daemon_threads = True


def create_server(backend, ui_root, token=None):
    session = token or secrets.token_hex(24)
    ui = Path(ui_root)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_data(self, data, status=200, content_type='application/json; charset=utf-8'):
            body = json.dumps(data, ensure_ascii=False).encode() if isinstance(data, dict) else data
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            expected = f'127.0.0.1:{self.server.server_port}'
            origin = self.headers.get('Origin')
            return (self.headers.get('Host') == expected
                    and self.headers.get('X-Session-Key') == session
                    and (origin is None or origin == 'http://' + expected))

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/api/status':
                if not self.authorized():
                    return self.send_data({'error': '로컬 세션 인증 실패'}, 403)
                try:
                    return self.send_data(backend.status())
                except Exception as error:
                    return self.send_data({'error': str(error)}, 400)
            names = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.css': ('app.css', 'text/css; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8')}
            if path not in names or self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
                return self.send_data({'error': '찾을 수 없습니다.'}, 404)
            name, content_type = names[path]
            return self.send_data((ui / name).read_bytes(), content_type=content_type)

        def do_POST(self):
            if not self.authorized():
                return self.send_data({'error': '로컬 세션 인증 실패'}, 403)
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.send_data({'error': 'JSON만 받습니다.'}, 415)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 128 * 1024:
                    raise ValueError('요청 크기가 올바르지 않습니다.')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('JSON 객체가 필요합니다.')
                path = urlsplit(self.path).path
                if path == '/api/folders':
                    return self.send_data({'path': choose_folder(body.get('kind'))})
                if not path.startswith('/api/'):
                    return self.send_data({'error': '알 수 없는 작업입니다.'}, 404)
                result = backend.run(path[5:], body)
                # Backend.run releases its busy flag in finally, after creating result.
                result['busy'] = False
                return self.send_data(result)
            except Exception as error:
                return self.send_data({'error': str(error)}, 400)

    server = Server(('127.0.0.1', 0), Handler)
    server.session_token = session
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    resources = here.parent if (here.parent / 'data/identities.json').exists() else here
    default = Path(os.environ.get('LOCALAPPDATA', str(here / 'userdata'))) / 'FM26CelebrityHelper'
    backend = Backend(resources, args.data_dir or default)
    server = create_server(backend, here / 'ui')
    url = f'http://127.0.0.1:{server.server_port}/#{server.session_token}'
    print('FM26 Celebrity Helper - experimental local tool')
    print('This window keeps the helper running. Close it to stop.')
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        backend.close()


if __name__ == '__main__':
    main()
