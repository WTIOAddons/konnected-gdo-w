"""A fake Konnected GDO White web server for tests.

Mimics the ESPHome web server: JSON state on GET, actions on POST, and a
server-sent-events stream on /events that sends a burst of current state
on connect followed by anything queued with ``push``.
"""

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote, urlparse, parse_qs


class FakeGdo:
    def __init__(self, legacy_urls=False):
        self.legacy_urls = legacy_urls
        self.state = {
            'cover/Garage Door': {'state': 'CLOSED',
                                  'current_operation': 'IDLE', 'value': 0},
            'binary_sensor/Wired Sensor': {'state': 'OFF', 'value': False},
            'sensor/Sensor distance': {'state': '0.35 m', 'value': 0.35},
            'number/Sensor calibration': {'state': '2.40 m', 'value': 2.4},
            'switch/STR output': {'state': 'OFF', 'value': False},
            'sensor/WiFi Signal': {'state': '-61.0 dBm', 'value': -61.0},
            'sensor/Uptime': {'state': '123 s', 'value': 123.0},
            'text_sensor/Device ID': {'state': 'a1b2c3d4e5f6',
                                      'value': 'a1b2c3d4e5f6'},
            'text_sensor/IP Address': {'state': '192.168.1.50',
                                       'value': '192.168.1.50'},
            'text_sensor/ESPHome Version': {'state': '2026.8.1',
                                            'value': '2026.8.1'},
        }
        self.buttons = ['button/Pre-close Warning', 'button/Restart']
        self.posts = []
        self.streams = []
        self._lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def _match(self, path):
                parts = path.strip('/').split('/', 1)
                if len(parts) != 2:
                    return None
                domain, rest = parts
                rest = unquote(rest)
                for key in list(server.state) + server.buttons:
                    d, n = key.split('/', 1)
                    if d != domain:
                        continue
                    if server.legacy_urls:
                        slug = ''.join(c if c.isalnum() else '_'
                                       for c in n.lower())
                        if rest == slug or rest.startswith(slug + '/'):
                            return key, rest[len(slug) + 1:]
                    else:
                        if rest == n or rest.startswith(n + '/'):
                            return key, rest[len(n) + 1:]
                return None

            def _json(self, key):
                body = dict(server.state[key])
                body['id'] = key
                return json.dumps(body).encode()

            def do_GET(self):
                url = urlparse(self.path)
                if url.path == '/events':
                    return self._events()
                m = self._match(url.path)
                if m is None or m[1] or m[0] in server.buttons:
                    self.send_response(404)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                body = self._json(m[0])
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                url = urlparse(self.path)
                m = self._match(url.path)
                if m is None or not m[1]:
                    self.send_response(404)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                key, action = m
                params = {k: v[0] for k, v in parse_qs(url.query).items()}
                server.posts.append((key, action, params))
                server._apply(key, action, params)
                self.send_response(200)
                self.send_header('Content-Length', '0')
                self.end_headers()

            def _events(self):
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Connection', 'keep-alive')
                self.end_headers()
                q = queue.Queue()
                with server._lock:
                    server.streams.append(q)
                try:
                    self.wfile.write(b'event: ping\r\ndata: \r\n\r\n')
                    for key in server.state:
                        self._send_state(key)
                    self.wfile.flush()
                    while True:
                        item = q.get()
                        if item is None:
                            return
                        if item == 'ping':
                            self.wfile.write(b'event: ping\r\ndata: \r\n\r\n')
                        else:
                            self._send_state(item)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                finally:
                    with server._lock:
                        if q in server.streams:
                            server.streams.remove(q)

            def _send_state(self, key):
                self.wfile.write(b'event: state\r\ndata: ' +
                                 self._json(key) + b'\r\n\r\n')

        self.httpd = HTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.daemon_threads = True
        # Serve each request in its own thread so /events can block.
        import socketserver
        self.httpd.__class__ = type('ThreadedHTTPServer',
                                    (socketserver.ThreadingMixIn,
                                     HTTPServer), {'daemon_threads': True,
                                                   'block_on_close': False})
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever)
        self.thread.daemon = True

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        with self._lock:
            for q in list(self.streams):
                q.put(None)
        self.httpd.shutdown()
        self.httpd.server_close()

    def _apply(self, key, action, params):
        if key == 'cover/Garage Door':
            if action == 'open':
                self.state[key].update(state='OPEN', value=1,
                                       current_operation='OPENING')
            elif action == 'close':
                self.state[key].update(current_operation='CLOSING')
            elif action == 'stop':
                self.state[key].update(current_operation='IDLE')
        elif key == 'switch/STR output':
            on = action == 'turn_on' or (
                action == 'toggle' and not self.state[key]['value'])
            self.state[key].update(state='ON' if on else 'OFF', value=on)
        elif key == 'number/Sensor calibration' and action == 'set':
            v = float(params['value'])
            self.state[key].update(state='{:.2f} m'.format(v), value=v)

    def push(self, key, **fields):
        """Update state and stream it to every connected client."""
        self.state[key].update(fields)
        with self._lock:
            for q in self.streams:
                q.put(key)

    def ping(self):
        with self._lock:
            for q in self.streams:
                q.put('ping')
