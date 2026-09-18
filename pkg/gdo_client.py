"""HTTP client for the Konnected GDO White (ESPHome web server API).

Two channels are used:

* REST   ``GET /{domain}/{name}`` reads state, ``POST .../{action}`` acts.
* SSE    ``GET /events`` is a server-sent-events stream.  On connect the
         device sends the state of every entity, then pushes a message
         whenever something changes, plus periodic ``ping`` keepalives.

Only the standard library is used so the addon builds on every
platform / Python combination the WebThings toolchain supports.
"""

import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import entities

# Seconds to wait for a REST call.  The device answers in milliseconds;
# anything longer means it is gone.
REST_TIMEOUT = 5

# The device pings the event stream every few seconds.  If nothing at all
# arrives for this long the connection is considered dead.
SSE_READ_TIMEOUT = 90

# Reconnect back-off bounds for the event stream.
SSE_RETRY_MIN = 2
SSE_RETRY_MAX = 60


class GdoError(Exception):
    """Raised when the device cannot be reached or rejects a request."""


class GdoClient:
    """Thin REST client that resolves entity URLs across firmware versions."""

    def __init__(self, host, port=80, timeout=REST_TIMEOUT):
        self.host = host
        self.port = int(port or 80)
        self.timeout = timeout
        self._paths = {}   # entity.key -> path that worked
        self._lock = threading.Lock()

    @property
    def base_url(self):
        if self.port == 80:
            return 'http://{}'.format(self.host)
        return 'http://{}:{}'.format(self.host, self.port)

    def set_host(self, host, port=None):
        """Point the client at a new address (e.g. after a DHCP change)."""
        with self._lock:
            self.host = host
            if port:
                self.port = int(port)
            self._paths = {}

    # ---- low level -----------------------------------------------------

    def _request(self, method, path, params=None):
        url = self.base_url + path
        if params:
            url = url + '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, method=method)
        if method == 'POST':
            # ESPHome expects a body-less POST; give it an empty one so the
            # Content-Length header is present.
            req.data = b''
        logging.debug('%s %s', method, url)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
                return resp.status, body
        except urllib.error.HTTPError as ex:
            return ex.code, b''
        except (urllib.error.URLError, socket.timeout, OSError,
                ConnectionError) as ex:
            raise GdoError('{} {} failed: {}'.format(method, url, ex))

    def _resolve(self, method, entity, action=None, params=None):
        """Try each candidate path for the entity until one is not a 404."""
        with self._lock:
            known = self._paths.get(entity.key)
        candidates = []
        if known:
            candidates.append(known)
        for path in entity.paths():
            if path not in candidates:
                candidates.append(path)

        last_status = None
        for path in candidates:
            full = path if action is None else path + '/' + action
            status, body = self._request(method, full, params)
            if status == 404:
                last_status = status
                continue
            with self._lock:
                self._paths[entity.key] = path
            return status, body
        raise GdoError('{} not found on {} (HTTP {})'
                       .format(entity.name_id, self.base_url, last_status))

    # ---- public API ------------------------------------------------------

    def get(self, entity):
        """GET an entity's state; returns the decoded JSON dict."""
        status, body = self._resolve('GET', entity)
        if status != 200:
            raise GdoError('GET {} returned HTTP {}'
                           .format(entity.name_id, status))
        try:
            return json.loads(body.decode('utf-8'))
        except ValueError as ex:
            raise GdoError('GET {} bad JSON: {}'.format(entity.name_id, ex))

    def post(self, entity, action, params=None):
        """POST an action to an entity.  Returns True on HTTP 200."""
        status, _ = self._resolve('POST', entity, action, params)
        if status != 200:
            raise GdoError('POST {}/{} returned HTTP {}'.format(
                entity.name_id, action, status))
        return True

    # Convenience wrappers for the GDO White's entities.
    def cover_state(self):
        return self.get(entities.COVER)

    def open_door(self):
        return self.post(entities.COVER, 'open')

    def close_door(self):
        return self.post(entities.COVER, 'close')

    def stop_door(self):
        return self.post(entities.COVER, 'stop')

    def toggle_door(self):
        return self.post(entities.COVER, 'toggle')

    def pre_close_warning(self):
        return self.post(entities.PRE_CLOSE_WARNING, 'press')

    def restart(self):
        return self.post(entities.RESTART, 'press')

    def set_str_output(self, on):
        return self.post(entities.STR_OUTPUT,
                         'turn_on' if on else 'turn_off')

    def set_calibration(self, metres):
        return self.post(entities.CALIBRATION, 'set',
                         {'value': '{:.2f}'.format(float(metres))})

    def device_id(self):
        """Return the device's MAC-based id, or None if unavailable."""
        try:
            data = self.get(entities.DEVICE_ID)
        except GdoError:
            return None
        value = data.get('value') or data.get('state')
        if not value:
            return None
        return str(value).replace(':', '').lower()


def parse_sse(lines):
    """Generator: turn an iterable of raw SSE lines into (event, data).

    ``lines`` yields bytes (as from an HTTP response object).  Comment
    lines (``:``) and unknown fields are ignored.  A blank line ends an
    event.  ``data`` is the joined data payload as a string.
    """
    event = None
    data = []
    for raw in lines:
        line = raw.rstrip(b'\r\n').decode('utf-8', 'replace')
        if line == '':
            if data:
                yield event, '\n'.join(data)
            event = None
            data = []
            continue
        if line.startswith(':'):
            continue
        field, _, value = line.partition(':')
        if value.startswith(' '):
            value = value[1:]
        if field == 'event':
            event = value
        elif field == 'data':
            data.append(value)
    if data:
        yield event, '\n'.join(data)


class GdoEventStream(threading.Thread):
    """Background thread following ``/events`` and reporting state changes.

    on_state(entity_or_None, payload_dict) is called for every state
    event.  on_alive() is called whenever any bytes arrive (including
    pings) so the owner can track reachability.  on_disconnect(reason)
    is called when a connection drops.
    """

    def __init__(self, client, on_state, on_alive=None, on_disconnect=None):
        threading.Thread.__init__(self, name='gdo-sse-' + client.host)
        self.daemon = True
        self.client = client
        self.on_state = on_state
        self.on_alive = on_alive or (lambda: None)
        self.on_disconnect = on_disconnect or (lambda reason: None)
        self._stop = threading.Event()
        self._response = None

    def stop(self):
        self._stop.set()
        resp = self._response
        if resp is not None:
            try:
                # Closing the underlying socket unblocks the reader.
                resp.fp.raw._sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                resp.close()
            except Exception:
                pass

    def run(self):
        delay = SSE_RETRY_MIN
        while not self._stop.is_set():
            try:
                self._follow()
                delay = SSE_RETRY_MIN
            except Exception as ex:
                if self._stop.is_set():
                    break
                logging.info('Event stream from %s dropped: %s',
                             self.client.host, ex)
                try:
                    self.on_disconnect(str(ex))
                except Exception:
                    logging.exception('on_disconnect failed')
            if self._stop.wait(delay):
                break
            delay = min(delay * 2, SSE_RETRY_MAX)
        logging.debug('Event stream thread for %s finished',
                      self.client.host)

    def _follow(self):
        url = self.client.base_url + '/events'
        req = urllib.request.Request(url, headers={
            'Accept': 'text/event-stream',
            'Cache-Control': 'no-cache',
        })
        logging.debug('Connecting to %s', url)
        resp = urllib.request.urlopen(req, timeout=SSE_READ_TIMEOUT)
        self._response = resp
        try:
            if resp.status != 200:
                raise GdoError('HTTP {} from /events'.format(resp.status))
            logging.info('Event stream connected to %s', self.client.host)
            delay_reset_at = time.time()
            for event, data in parse_sse(self._alive_lines(resp)):
                if self._stop.is_set():
                    return
                if event in (None, 'state'):
                    self._handle_state(data)
                elif event == 'ping':
                    pass
                elif event == 'log':
                    logging.debug('device log: %s', data)
                # Anything else (e.g. future event types) is ignored.
                if time.time() - delay_reset_at > 3600:
                    delay_reset_at = time.time()
            raise GdoError('event stream closed by device')
        finally:
            self._response = None
            try:
                resp.close()
            except Exception:
                pass

    def _alive_lines(self, resp):
        """Yield lines from the response, reporting liveness on each."""
        while True:
            line = resp.readline()
            if not line:
                return
            self.on_alive()
            yield line

    def _handle_state(self, data):
        if not data or not data.strip():
            return
        try:
            payload = json.loads(data)
        except ValueError:
            logging.debug('Ignoring non-JSON event data: %r', data[:80])
            return
        if not isinstance(payload, dict):
            return
        entity = entities.lookup(payload)
        try:
            self.on_state(entity, payload)
        except Exception:
            logging.exception('on_state failed for %s', payload)
