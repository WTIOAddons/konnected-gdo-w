import time
import unittest

from pkg import entities
from pkg.gdo_device import GdoDevice
from tests.fake_gdo import FakeGdo


class FakeProxy:
    def __init__(self):
        self.props = []
        self.events = []
        self.connected = []

    def send_property_changed_notification(self, prop):
        self.props.append((prop.name, prop.get_value()))

    def send_event_notification(self, event):
        self.events.append(event.name)

    def send_connected_notification(self, device, connected):
        self.connected.append(connected)


class FakeAdapter:
    def __init__(self):
        self.manager_proxy = FakeProxy()
        self.rediscovered = []

    def rediscover(self, device):
        self.rediscovered.append(device.id)
        return False


class FakeAction:
    def __init__(self, name):
        self.name = name
        self.started = self.finished = False

    def start(self):
        self.started = True

    def finish(self):
        self.finished = True


class DeviceStateTests(unittest.TestCase):
    def setUp(self):
        self.adapter = FakeAdapter()
        self.device = GdoDevice(self.adapter, 'konnected-gdo-w-test',
                                '127.0.0.1', 1, title='Test Door')

    def test_description(self):
        thing = self.device.as_thing()
        self.assertEqual(thing['@type'], ['DoorSensor', 'OnOffSwitch'])
        self.assertEqual(thing['title'], 'Test Door')
        for name in ('on', 'door', 'status', 'wired_sensor', 'distance',
                     'calibration', 'str_output', 'wifi_signal', 'uptime',
                     'ip_address', 'firmware'):
            self.assertIn(name, thing['properties'])
        for name in ('open', 'close', 'stop', 'toggle', 'preclose_warning',
                     'calibrate', 'restart'):
            self.assertIn(name, thing['actions'])
        self.assertEqual(sorted(thing['events']),
                         ['door_closed', 'door_closing', 'door_opened',
                          'door_opening'])

    def test_cover_transitions_fire_events(self):
        dev = self.device
        dev.handle_state(entities.COVER, {'state': 'CLOSED',
                                          'current_operation': 'IDLE',
                                          'value': 0})
        self.assertEqual(dev.properties['status'].get_value(), 'closed')
        self.assertFalse(dev.properties['door'].get_value())
        # First sync never fires an event.
        self.assertEqual(self.adapter.manager_proxy.events, [])

        dev.handle_state(entities.COVER, {'state': 'CLOSED',
                                          'current_operation': 'OPENING',
                                          'value': 0})
        dev.handle_state(entities.COVER, {'state': 'OPEN',
                                          'current_operation': 'IDLE',
                                          'value': 1})
        dev.handle_state(entities.COVER, {'state': 'OPEN',
                                          'current_operation': 'CLOSING',
                                          'value': 1})
        dev.handle_state(entities.COVER, {'state': 'CLOSED',
                                          'current_operation': 'IDLE',
                                          'value': 0})
        self.assertEqual(self.adapter.manager_proxy.events,
                         ['door_opening', 'door_opened', 'door_closing',
                          'door_closed'])
        self.assertTrue(any(n == 'on' and v is True
                            for n, v in self.adapter.manager_proxy.props))

    def test_sensor_payloads(self):
        dev = self.device
        dev.handle_state(entities.DISTANCE, {'state': '2.40 m',
                                             'value': 2.4013})
        self.assertEqual(dev.properties['distance'].get_value(), 2.4)
        dev.handle_state(entities.WIRED_SENSOR, {'state': 'ON'})
        self.assertTrue(dev.properties['wired_sensor'].get_value())
        dev.handle_state(entities.UPTIME, {'state': '99 s', 'value': 99.0})
        self.assertEqual(dev.properties['uptime'].get_value(), 99)
        dev.handle_state(entities.ESPHOME_VERSION, {'state': '2026.8.1',
                                                    'value': '2026.8.1'})
        self.assertEqual(dev.properties['firmware'].get_value(), '2026.8.1')
        dev.handle_state(entities.DEVICE_ID, {'value': 'A1:B2:C3:D4:E5:F6'})
        self.assertEqual(dev.mac, 'a1b2c3d4e5f6')
        # Unknown entities are ignored without error.
        dev.handle_state(None, {'id': 'light-warning_led', 'state': 'ON'})

    def test_action_on_unreachable_device_finishes(self):
        action = FakeAction('open')
        self.device.perform_action(action)
        self.assertTrue(action.started and action.finished)


class DeviceLiveTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGdo().start()
        self.adapter = FakeAdapter()
        self.device = GdoDevice(self.adapter, 'konnected-gdo-w-live',
                                '127.0.0.1', self.fake.port,
                                poll_interval=5)

    def tearDown(self):
        self.device.stop()
        self.fake.stop()

    def _wait(self, pred, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if pred():
                return True
            time.sleep(0.05)
        return False

    def test_start_syncs_and_follows_stream(self):
        self.device.start()
        dev = self.device
        self.assertEqual(dev.properties['status'].get_value(), 'closed')
        self.assertEqual(dev.properties['ip_address'].get_value(),
                         '192.168.1.50')
        self.assertEqual(dev.mac, 'a1b2c3d4e5f6')
        self.assertEqual(self.adapter.manager_proxy.connected, [True])

        self.assertTrue(self._wait(lambda: self.fake.streams))
        self.fake.push('cover/Garage Door', state='OPEN', value=1,
                       current_operation='IDLE')
        self.assertTrue(self._wait(
            lambda: dev.properties['status'].get_value() == 'open'))
        self.assertIn('door_opened', self.adapter.manager_proxy.events)

        self.fake.push('switch/STR output', state='ON', value=True)
        self.assertTrue(self._wait(
            lambda: dev.properties['str_output'].get_value() is True))

    def test_writable_properties_and_actions(self):
        self.device.start()
        dev = self.device
        dev.properties['on'].set_value(True)
        dev.properties['str_output'].set_value(True)
        dev.properties['calibration'].set_value(1.5)
        action = FakeAction('calibrate')
        dev.perform_action(action)
        self.assertTrue(action.finished)
        actions = [(k, a) for k, a, _ in self.fake.posts]
        self.assertEqual(actions, [
            ('cover/Garage Door', 'open'),
            ('switch/STR output', 'turn_on'),
            ('number/Sensor calibration', 'set'),
            ('number/Sensor calibration', 'set'),
        ])
        # calibrate copies the live distance reading (0.35 m)
        self.assertEqual(self.fake.posts[-1][2], {'value': '0.35'})
        self.assertEqual(dev.properties['calibration'].get_value(), 0.35)


if __name__ == '__main__':
    unittest.main()
