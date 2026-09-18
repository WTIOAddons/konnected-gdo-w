"""Properties for the Konnected GDO White addon for WebThings Gateway."""

import logging

from gateway_addon import Property
from gateway_addon.errors import PropertyError

from .gdo_client import GdoError


class GdoProperty(Property):
    """A read-only property whose value is pushed in from the device."""

    def __init__(self, device, name, description):
        """
        device -- the Device this property belongs to
        name -- name of the property (used in rules and the REST API)
        description -- WebThings property description dictionary
        """
        Property.__init__(self, device, name, description)

    def update(self, value):
        """Store a new value from the device and notify the gateway."""
        if value is None:
            return False
        try:
            return self.set_cached_value_and_notify(value)
        except Exception as ex:
            logging.exception('Update of %s failed: %s', self.name, ex)
            return False


class GdoWritableProperty(GdoProperty):
    """A property the user may change; writes go to the device."""

    def __init__(self, device, name, description, setter):
        """
        setter -- callable(value) that pushes the value to the device.
                  It may raise GdoError.
        """
        GdoProperty.__init__(self, device, name, description)
        self._setter = setter

    def set_value(self, value):
        desc = self.description
        if 'minimum' in desc and value < desc['minimum']:
            raise PropertyError('Value less than minimum: {}'.format(
                desc['minimum']))
        if 'maximum' in desc and value > desc['maximum']:
            raise PropertyError('Value greater than maximum: {}'.format(
                desc['maximum']))
        if 'enum' in desc and value not in desc['enum']:
            raise PropertyError('Invalid enum value')
        try:
            self._setter(value)
        except GdoError as ex:
            logging.warning('Setting %s failed: %s', self.name, ex)
            raise PropertyError(str(ex))
        # Optimistically reflect the new value; the device's event stream
        # will confirm (or correct) it shortly.
        self.set_cached_value_and_notify(value)


def door_open_property(device):
    return GdoProperty(device, 'door', {
        'title': 'Door',
        '@type': 'OpenProperty',
        'type': 'boolean',
        'readOnly': True,
        'description': 'True while the garage door is open',
    })


def door_status_property(device):
    return GdoProperty(device, 'status', {
        'title': 'Status',
        'type': 'string',
        'readOnly': True,
        'enum': ['open', 'closed', 'opening', 'closing', 'unknown'],
        'description': 'Door state including motion',
    })


def door_control_property(device, setter):
    return GdoWritableProperty(device, 'on', {
        'title': 'Open door',
        '@type': 'OnOffProperty',
        'type': 'boolean',
        'description': 'On opens the door, off closes it',
    }, setter)


def wired_sensor_property(device):
    return GdoProperty(device, 'wired_sensor', {
        'title': 'Wired Sensor',
        'type': 'boolean',
        'readOnly': True,
        'description': 'Wired contact input (on = door open)',
    })


def distance_property(device):
    return GdoProperty(device, 'distance', {
        'title': 'Sensor distance',
        'type': 'number',
        'unit': 'm',
        'readOnly': True,
        'description': 'Optical range sensor reading',
    })


def calibration_property(device, setter):
    return GdoWritableProperty(device, 'calibration', {
        'title': 'Sensor calibration',
        'type': 'number',
        'unit': 'm',
        'minimum': 0.01,
        'maximum': 2.0,
        'multipleOf': 0.01,
        'description': 'Distance measured when the door is fully open',
    }, setter)


def str_output_property(device, setter):
    return GdoWritableProperty(device, 'str_output', {
        'title': 'STR output',
        '@type': 'OnOffProperty',
        'type': 'boolean',
        'description': '12V switched output labelled STR',
    }, setter)


def wifi_signal_property(device):
    return GdoProperty(device, 'wifi_signal', {
        'title': 'WiFi Signal',
        'type': 'number',
        'unit': 'dBm',
        'readOnly': True,
    })


def uptime_property(device):
    return GdoProperty(device, 'uptime', {
        'title': 'Uptime',
        'type': 'integer',
        'unit': 'second',
        'readOnly': True,
    })


def ip_address_property(device):
    return GdoProperty(device, 'ip_address', {
        'title': 'IP Address',
        'type': 'string',
        'readOnly': True,
    })


def firmware_property(device):
    return GdoProperty(device, 'firmware', {
        'title': 'Firmware',
        'type': 'string',
        'readOnly': True,
        'description': 'ESPHome version running on the device',
    })
