"""Device for the Konnected GDO White addon for WebThings Gateway."""

import logging
import threading
import time

from gateway_addon import Device, Event

from . import entities
from .gdo_client import GdoClient, GdoEventStream, GdoError
from . import gdo_property as props

# Wait at least this long between rediscovery attempts for a device that
# was found over mDNS and has gone quiet (its IP may have changed).
REDISCOVER_RETRY_SECONDS = 60

# WebThings event fired when the door enters each status.
EVENT_FOR_STATUS = {
    'open': 'door_opened',
    'closed': 'door_closed',
    'opening': 'door_opening',
    'closing': 'door_closing',
}


def _to_bool(payload):
    if 'value' in payload and payload['value'] is not None:
        return bool(payload['value'])
    state = str(payload.get('state', '')).upper()
    if state in ('ON', 'OPEN', 'TRUE'):
        return True
    if state in ('OFF', 'CLOSED', 'FALSE'):
        return False
    return None


def _to_float(payload):
    value = payload.get('value')
    if isinstance(value, (int, float)):
        return float(value)
    state = str(payload.get('state', '')).strip().split(' ')[0]
    try:
        return float(state)
    except ValueError:
        return None


def _to_str(payload):
    value = payload.get('value')
    if value is None or value == '':
        value = payload.get('state')
    if value is None:
        return None
    return str(value)


class GdoDevice(Device):
    """A Konnected GDO White garage door opener."""

    def __init__(self, adapter, _id, host, port=80, title='',
                 mac=None, discovered=False, poll_interval=30):
        """
        adapter -- the Adapter for this device
        _id -- WebThings device id
        host, port -- where the device's web server is
        title -- Thing title shown in the gateway
        mac -- device MAC (from mDNS) if known
        discovered -- True when found via mDNS (enables rediscovery)
        poll_interval -- seconds between REST health checks
        """
        Device.__init__(self, adapter, _id)
        self._context = 'https://webthings.io/schemas'
        self._type = ['DoorSensor', 'OnOffSwitch']
        self.title = title or 'Garage Door'
        self.name = self.title
        self.description = 'Konnected Garage Door Opener (GDO White)'
        self.mac = mac
        self.discovered = discovered
        self.poll_interval = max(5, int(poll_interval))
        self.client = GdoClient(host, port)

        self.last_seen = 0.0
        self._connected = None
        self._last_rediscover = 0.0
        self._status = 'unknown'
        self._synced_once = False
        self._stop = threading.Event()
        self._stream = None
        self._watchdog = None

        self._build_properties()
        self._build_actions()
        self._build_events()

    # ---- description ---------------------------------------------------

    def _build_properties(self):
        for prop in (
                props.door_control_property(self, self._set_door),
                props.door_open_property(self),
                props.door_status_property(self),
                props.wired_sensor_property(self),
                props.distance_property(self),
                props.calibration_property(self, self.client.set_calibration),
                props.str_output_property(self, self.client.set_str_output),
                props.wifi_signal_property(self),
                props.uptime_property(self),
                props.ip_address_property(self),
                props.firmware_property(self)):
            self.properties[prop.name] = prop

    def _build_actions(self):
        self.add_action('open', {
            'title': 'Open',
            'description': 'Open the garage door (ignored if already open)',
        })
        self.add_action('close', {
            'title': 'Close',
            'description': 'Sound the pre-close warning, then close the door',
        })
        self.add_action('stop', {
            'title': 'Stop',
            'description': 'Stop the door if it is moving',
        })
        self.add_action('toggle', {
            'title': 'Toggle',
            'description': 'Press the opener button',
            '@type': 'ToggleAction',
        })
        self.add_action('preclose_warning', {
            'title': 'Pre-close warning',
            'description': 'Flash the LED and beep without moving the door',
        })
        self.add_action('calibrate', {
            'title': 'Calibrate open distance',
            'description': 'Store the current range reading as the '
                           'fully-open distance. Run with the door open.',
        })
        self.add_action('restart', {
            'title': 'Restart device',
            'description': 'Reboot the GDO controller',
        })

    def _build_events(self):
        for name, title in (('door_opened', 'Door opened'),
                            ('door_closed', 'Door closed'),
                            ('door_opening', 'Door opening'),
                            ('door_closing', 'Door closing')):
            self.add_event(name, {
                'title': title,
                'description': title,
                'type': 'string',
            })

    # ---- lifecycle -----------------------------------------------------

    def start(self):
        """Sync once over REST, then follow the event stream."""
        self.sync()
        self._stream = GdoEventStream(self.client,
                                      on_state=self.handle_state,
                                      on_alive=self.mark_seen,
                                      on_disconnect=self._stream_dropped)
        self._stream.start()
        self._watchdog = threading.Thread(target=self._watch,
                                          name='gdo-watch-' + self.id)
        self._watchdog.daemon = True
        self._watchdog.start()
        logging.info('GDO device %s started (%s)', self.id,
                     self.client.base_url)

    def stop(self):
        self._stop.set()
        if self._stream is not None:
            self._stream.stop()
        if self._watchdog is not None and \
                self._watchdog is not threading.current_thread():
            self._watchdog.join(self.poll_interval + 5)
        logging.debug('GDO device %s stopped', self.id)

    def sync(self):
        """Read the door state and a few slow sensors over REST."""
        try:
            self.handle_state(entities.COVER, self.client.cover_state())
        except GdoError as ex:
            logging.debug('Sync of %s failed: %s', self.id, ex)
            return False
        self.mark_seen()
        for ent in (entities.WIRED_SENSOR, entities.DISTANCE,
                    entities.CALIBRATION, entities.STR_OUTPUT,
                    entities.IP_ADDRESS, entities.ESPHOME_VERSION):
            try:
                self.handle_state(ent, self.client.get(ent))
            except GdoError as ex:
                logging.debug('Sync of %s %s: %s', self.id, ent.key, ex)
        if self.mac is None:
            self.mac = self.client.device_id()
        return True

    def _watch(self):
        """Fallback health check when the event stream is silent."""
        while not self._stop.wait(self.poll_interval):
            try:
                quiet_for = time.time() - self.last_seen
                if quiet_for < 2 * self.poll_interval:
                    continue
                if self.sync():
                    continue
                self.set_connected(False)
                if self.discovered:
                    self._maybe_rediscover()
            except Exception as ex:
                logging.exception('Watchdog for %s failed: %s', self.id, ex)

    def _stream_dropped(self, reason):
        # The stream thread reconnects on its own; a quick REST probe tells
        # us whether the device itself is gone.
        if not self.sync():
            self.set_connected(False)

    def _maybe_rediscover(self):
        now = time.time()
        if now - self._last_rediscover < REDISCOVER_RETRY_SECONDS:
            return
        self._last_rediscover = now
        try:
            self.adapter.rediscover(self)
        except Exception as ex:
            logging.exception('Rediscovery for %s failed: %s', self.id, ex)

    # ---- connectivity --------------------------------------------------

    def mark_seen(self):
        self.last_seen = time.time()
        self.set_connected(True)

    def set_connected(self, connected):
        if self._connected == connected:
            return
        self._connected = connected
        logging.info('GDO device %s %s', self.id,
                     'connected' if connected else 'unreachable')
        try:
            self.connected_notify(connected)
        except Exception as ex:
            logging.debug('connected_notify failed: %s', ex)

    # ---- state from the device -----------------------------------------

    def handle_state(self, entity, payload):
        """Apply one state payload (from SSE or REST) to the properties."""
        if entity is None:
            logging.debug('Unknown entity in %s', payload)
            return
        key = entity.key
        if key == 'cover':
            self._handle_cover(payload)
        elif key == 'wired_sensor':
            self.properties['wired_sensor'].update(_to_bool(payload))
        elif key == 'distance':
            value = _to_float(payload)
            if value is not None:
                self.properties['distance'].update(round(value, 2))
        elif key == 'calibration':
            value = _to_float(payload)
            if value is not None:
                self.properties['calibration'].update(round(value, 2))
        elif key == 'str_output':
            self.properties['str_output'].update(_to_bool(payload))
        elif key == 'wifi_signal':
            value = _to_float(payload)
            if value is not None:
                self.properties['wifi_signal'].update(round(value, 1))
        elif key == 'uptime':
            value = _to_float(payload)
            if value is not None:
                self.properties['uptime'].update(int(value))
        elif key == 'ip_address':
            self.properties['ip_address'].update(_to_str(payload))
        elif key == 'esphome_version':
            self.properties['firmware'].update(_to_str(payload))
        elif key == 'device_id':
            value = _to_str(payload)
            if value:
                self.mac = value.replace(':', '').lower()

    def _handle_cover(self, payload):
        is_open = _to_bool(payload)
        operation = str(payload.get('current_operation', 'IDLE')).upper()
        if operation == 'OPENING':
            status = 'opening'
        elif operation == 'CLOSING':
            status = 'closing'
        elif is_open is None:
            status = 'unknown'
        else:
            status = 'open' if is_open else 'closed'

        if is_open is not None:
            self.properties['door'].update(is_open)
            self.properties['on'].update(is_open)
        changed = self.properties['status'].update(status)

        if changed and self._synced_once and status in EVENT_FOR_STATUS:
            self._fire(EVENT_FOR_STATUS[status], status)
        self._status = status
        self._synced_once = True

    def _fire(self, name, data):
        try:
            self.event_notify(Event(self, name, data))
            logging.info('%s event %s', self.id, name)
        except Exception as ex:
            logging.debug('event_notify failed: %s', ex)

    # ---- commands ------------------------------------------------------

    def _set_door(self, value):
        if value:
            self.client.open_door()
        else:
            self.client.close_door()

    def perform_action(self, action):
        logging.debug('perform_action %s', action.name)
        action.start()
        try:
            if action.name == 'open':
                self.client.open_door()
            elif action.name == 'close':
                self.client.close_door()
            elif action.name == 'stop':
                self.client.stop_door()
            elif action.name == 'toggle':
                self.client.toggle_door()
            elif action.name == 'preclose_warning':
                self.client.pre_close_warning()
            elif action.name == 'calibrate':
                self._calibrate()
            elif action.name == 'restart':
                self.client.restart()
            else:
                logging.warning('Unknown action %s', action.name)
        except GdoError as ex:
            logging.warning('Action %s on %s failed: %s', action.name,
                            self.id, ex)
        action.finish()

    def _calibrate(self):
        reading = _to_float(self.client.get(entities.DISTANCE))
        if reading is None or reading <= 0:
            raise GdoError('No valid range reading to calibrate with')
        self.client.set_calibration(reading)
        self.properties['calibration'].update(round(reading, 2))
