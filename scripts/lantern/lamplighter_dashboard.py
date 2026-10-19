#!/usr/bin/env python3
"""A/B dashboard. Fixed SSH worker operations; no remote shell or private SAGE API."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hmac
import http.server
import json
from pathlib import Path
import subprocess
import threading
import time

HERE = Path(__file__).resolve().parent


class Dashboard:
    def __init__(self, config):
        self.config = config
        self.token = Path(config['token_file']).read_text().strip()
        self.lock = threading.Lock()
        self.displays = {}
        self.units = {u: {'unit': u, 'connected': False, 'error': 'Connecting…'} for u in ('unit-a', 'unit-b')}

    def command(self, unit, request):
        if unit not in self.units:
            raise ValueError('unknown unit')
        if unit == 'unit-a':
            command = [self.config['python'], '-B', str(HERE / 'lamplighter_dashboard_agent.py')]
        else:
            command = ['ssh', '-i', self.config['ssh_key'], '-o', 'IdentitiesOnly=yes',
                '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=5',
                '-o', 'UserKnownHostsFile=' + self.config['known_hosts'], 'radxa@192.168.50.96']
        r = subprocess.run(command, input=json.dumps(request), capture_output=True, text=True, timeout=20)
        try:
            result = json.loads(r.stdout)
        except ValueError:
            raise ValueError('Device unavailable over SSH; check power and network')
        if r.returncode or result.get('error'):
            raise ValueError(result.get('error', 'Device operation failed'))
        return result

    def refresh(self):
        def read(unit):
            try:
                return unit, dict(self.command(unit, {'action': 'snapshot'}), connected=True, observed=time.time())
            except Exception as error:
                return unit, {'unit': unit, 'connected': False, 'error': str(error), 'observed': time.time()}
        with ThreadPoolExecutor(max_workers=2) as pool:
            while True:
                for unit, state in pool.map(read, tuple(self.units)):
                    with self.lock:
                        self.units[unit] = state
                time.sleep(1)

    def server(self, bind, port):
        dashboard = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def setup(self):
                super().setup()
                self.connection.settimeout(10)
            def trusted_host(self):
                return self.headers.get('Host') in dashboard.config['hosts']
            def authorized(self):
                auth = self.headers.get('Authorization', '')
                return hmac.compare_digest(auth, 'Bearer ' + dashboard.token)
            def reply(self, status, value, html=False, content_type=None):
                raw = value if html else json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Type', content_type or ('text/html; charset=utf-8' if html else 'application/json'))
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('X-Frame-Options', 'DENY')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def do_GET(self):
                if not self.trusted_host():
                    return self.reply(403, {'error': 'Unrecognized host'})
                if self.path == '/ready.js':
                    return self.reply(200, b'/* dashboard ready */', html=True, content_type='application/javascript')
                if self.path.split('?', 1)[0] == '/':
                    return self.reply(200, (HERE / 'lamplighter_dashboard.html').read_bytes(), html=True)
                if self.path == '/api/session' and self.client_address[0] == '127.0.0.1' and self.headers.get('Host') in ('127.0.0.1:18791', 'localhost:18791'):
                    return self.reply(200, {'token': dashboard.token})
                if not self.authorized():
                    return self.reply(401, {'error': 'Connect with the dashboard access code'})
                if self.path == '/api/state':
                    with dashboard.lock:
                        state = dict(dashboard.units)
                    return self.reply(200, {'units': state, 'displays': dict(dashboard.displays), 'time': time.time()})
                return self.reply(404, {'error': 'Not found'})
            def do_POST(self):
                if not self.trusted_host() or not self.authorized():
                    return self.reply(403, {'error': 'Unauthorized'})
                if self.headers.get('Origin') != 'http://' + self.headers.get('Host', ''):
                    return self.reply(403, {'error': 'Origin mismatch'})
                if self.path not in ('/api/jobs', '/api/display') or self.headers.get('Content-Type') != 'application/json':
                    return self.reply(400, {'error': 'Invalid request'})
                try:
                    n = int(self.headers.get('Content-Length', '0'))
                    if not 0 < n <= 4096:
                        raise ValueError('Invalid request size')
                    body = json.loads(self.rfile.read(n))
                    if not isinstance(body, dict):
                        raise ValueError('Invalid request object')
                    unit = body.pop('unit')
                    if self.path == '/api/display':
                        if unit not in dashboard.units:
                            raise ValueError('Unknown display')
                        width, height = int(body['width']), int(body['height'])
                        if not (100 <= width <= 10000 and 100 <= height <= 10000):
                            raise ValueError('Invalid screen dimensions')
                        with dashboard.lock:
                            dashboard.displays[unit] = {'observed': time.time(), 'width': width, 'height': height}
                        return self.reply(200, {'ok': True})
                    if body.get('action') not in ('start', 'pause', 'resume'):
                        raise ValueError('Unknown operation')
                    result = dashboard.command(unit, body)
                    self.reply(200, result)
                except (ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
                    self.reply(400, {'error': str(error)})
        return http.server.ThreadingHTTPServer((bind, port), Handler)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config', required=True, type=Path)
    p.add_argument('--bind', default='0.0.0.0')
    p.add_argument('--port', default=18791, type=int)
    args = p.parse_args()
    dashboard = Dashboard(json.loads(args.config.read_bytes()))
    threading.Thread(target=dashboard.refresh, daemon=True).start()
    with dashboard.server(args.bind, args.port) as server:
        server.serve_forever()


if __name__ == '__main__':
    main()
