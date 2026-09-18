import unittest

from pkg import entities


class EntityTests(unittest.TestCase):
    def test_paths_new_then_legacy(self):
        paths = list(entities.WIRED_SENSOR.paths())
        self.assertEqual(paths[0], '/binary_sensor/Wired%20Sensor')
        self.assertIn('/binary_sensor/wired_sensor', paths)
        self.assertIn('/binary_sensor/garage_door_input', paths)

    def test_lookup_formats(self):
        # ESPHome >= 2026.8: id is the new format
        self.assertEqual(entities.lookup({'id': 'cover/Garage Door'}),
                         entities.COVER)
        # transition firmware: legacy id plus name_id
        self.assertEqual(entities.lookup({'id': 'sensor-range_sensor',
                                          'name_id':
                                          'sensor/Sensor distance'}),
                         entities.DISTANCE)
        # old firmware: legacy id only
        self.assertEqual(entities.lookup(
            {'id': 'binary-sensor-garage_door_input'}),
            entities.WIRED_SENSOR)
        self.assertEqual(entities.lookup({'id': 'text-sensor-device_id'}),
                         entities.DEVICE_ID)
        self.assertEqual(entities.lookup({'id': 'sensor-wifi_signal__'}),
                         entities.WIFI_SIGNAL_PCT)
        self.assertIsNone(entities.lookup({'id': 'light-warning_led'}))

    def test_object_id(self):
        self.assertEqual(entities.object_id('WiFi Signal %'),
                         'wifi_signal__')
        self.assertEqual(entities.object_id('Pre-close Warning'),
                         'pre_close_warning')
