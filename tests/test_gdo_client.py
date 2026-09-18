import threading
import time
import unittest

from pkg import entities
from pkg.gdo_client import GdoClient, GdoEventStream, GdoError, parse_sse
from tests.fake_gdo import FakeGdo


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.dev = FakeGdo().start()
        self.client = GdoClient('127.0.0.1', self.dev.port)

    def tearDown(self):
        self.dev.stop()

    def test_get_cover(self):
        data = self.client.cover_state()
        self.assertEqual(data['state'], 'CLOSED')
        self.assertEqual(entities.lookup(data), entities.COVER)

    def test_actions_post(self):
        self.assertTrue(self.client.open_door())
        self.assertTrue(self.client.set_str_output(True))
        self.assertTrue(self.client.set_calibration(1.234))
        self.assertTrue(self.client.pre_close_warning())
        self.assertEqual(self.dev.posts[0], ('cover/Garage Door', 'open', {}))
        self.assertEqual(self.dev.posts[1],
                         ('switch/STR output', 'turn_on', {}))
        self.assertEqual(self.dev.posts[2],
                         ('number/Sensor calibration', 'set',
                          {'value': '1.23'}))
        self.assertEqual(self.dev.state['cover/Garage Door']['state'],
                         'OPEN')

    def test_device_id(self):
        self.assertEqual(self.client.device_id(), 'a1b2c3d4e5f6')

    def test_unreachable(self):
        client = GdoClient('127.0.0.1', 1)
        with self.assertRaises(GdoError):
            client.cover_state()

    def test_unknown_entity_404(self):
        with self.assertRaises(GdoError):
            self.client.get(entities.RANGE_SENSOR)


class LegacyUrlTests(unittest.TestCase):
    """Firmware older than ESPHome 2026.7 only knows object_id URLs."""

    def setUp(self):
        self.dev = FakeGdo(legacy_urls=True).start()
        self.client = GdoClient('127.0.0.1', self.dev.port)

    def tearDown(self):
        self.dev.stop()

    def test_falls_back_to_object_id(self):
        data = self.client.cover_state()
        self.assertEqual(data['state'], 'CLOSED')
        self.assertEqual(self.client._paths['cover'], '/cover/garage_door')
        self.assertTrue(self.client.close_door())
        self.assertEqual(self.dev.posts[-1][1], 'close')


class SseParseTests(unittest.TestCase):
    def test_parse(self):
        raw = [b': comment\r\n', b'event: ping\r\n', b'data: \r\n', b'\r\n',
               b'event: state\r\n',
               b'data: {"id":"cover-garage_door","state":"OPEN"}\r\n',
               b'\r\n', b'data: {"a":1}\n', b'data: {"b":2}\n', b'\n']
        events = list(parse_sse(iter(raw)))
        self.assertEqual(events[0], ('ping', ''))
        self.assertEqual(events[1][0], 'state')
        self.assertIn('garage_door', events[1][1])
        self.assertEqual(events[2], (None, '{"a":1}\n{"b":2}'))


class EventStreamTests(unittest.TestCase):
    def setUp(self):
        self.dev = FakeGdo().start()
        self.client = GdoClient('127.0.0.1', self.dev.port)
        self.seen = []
        self.alive = threading.Event()
        self.got = threading.Condition()

    def tearDown(self):
        self.dev.stop()

    def _on_state(self, entity, payload):
        with self.got:
            self.seen.append((entity, payload))
            self.got.notify_all()

    def _wait_for(self, pred, timeout=5):
        deadline = time.time() + timeout
        with self.got:
            while not pred():
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                self.got.wait(remaining)
        return True

    def test_burst_and_push(self):
        stream = GdoEventStream(self.client, self._on_state,
                                on_alive=self.alive.set)
        stream.start()
        try:
            self.assertTrue(self._wait_for(
                lambda: len(self.seen) >= len(self.dev.state)))
            keys = [e.key for e, _ in self.seen if e is not None]
            self.assertIn('cover', keys)
            self.assertIn('distance', keys)
            self.assertIn('esphome_version', keys)
            self.assertTrue(self.alive.is_set())

            n = len(self.seen)
            self.dev.push('cover/Garage Door', state='OPEN', value=1,
                          current_operation='OPENING')
            self.assertTrue(self._wait_for(lambda: len(self.seen) > n))
            ent, payload = self.seen[-1]
            self.assertEqual(ent, entities.COVER)
            self.assertEqual(payload['current_operation'], 'OPENING')
        finally:
            stream.stop()
            stream.join(5)
            self.assertFalse(stream.is_alive())


if __name__ == '__main__':
    unittest.main()
