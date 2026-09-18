"""ESPHome entity descriptors for the Konnected GDO White.

The GDO White firmware (konnected-esphome garage-door-GDOv2-S.yaml and
garage-door-GDOv1-S.yaml) exposes its entities over the ESPHome web
server REST API.  The URL for an entity, and the ``id`` it reports on the
``/events`` stream, are derived from the entity's display name and the
format has changed between ESPHome releases:

* ESPHome < 2026.7   URL ``/{domain}/{object_id}``
                     e.g. ``/cover/garage_door``
* ESPHome >= 2026.7  URL ``/{domain}/{Display Name}``
                     e.g. ``/cover/Garage%20Door``

Each descriptor below carries the display name plus the legacy ids the
device may use so the client can match either format.
"""

import re
from urllib.parse import quote


def object_id(name):
    """Return the legacy ESPHome object_id slug for a display name."""
    return re.sub(r'[^a-z0-9]', '_', name.lower())


class Entity:
    """One ESPHome entity on the device."""

    def __init__(self, key, domain, name, legacy_ids=()):
        """
        key -- semantic key used inside this adapter (e.g. 'cover')
        domain -- ESPHome domain (cover, binary_sensor, sensor, ...)
        name -- entity display name as compiled into the firmware
        legacy_ids -- object_ids seen in older firmware for this entity,
                      in addition to the slug derived from the name
        """
        self.key = key
        self.domain = domain
        self.name = name
        slug = object_id(name)
        ids = [slug]
        for lid in legacy_ids:
            if lid not in ids:
                ids.append(lid)
        self.legacy_object_ids = ids

    @property
    def name_id(self):
        """New-format identifier: ``domain/Display Name``."""
        return '{}/{}'.format(self.domain, self.name)

    def sse_ids(self):
        """All identifiers this entity may use in an SSE event."""
        ids = [self.name_id]
        hyphen_domain = self.domain.replace('_', '-')
        for oid in self.legacy_object_ids:
            ids.append('{}-{}'.format(hyphen_domain, oid))
        return ids

    def paths(self):
        """Candidate REST paths, most likely first."""
        yield '/{}/{}'.format(self.domain, quote(self.name))
        for oid in self.legacy_object_ids:
            yield '/{}/{}'.format(self.domain, oid)

    def __repr__(self):
        return '<Entity {} {}>'.format(self.key, self.name_id)


# Entities of the stock GDO White firmware.  Entities marked ``internal``
# in the firmware (opener button, warning LED, status LED, "Play sound")
# are not reachable over the web API and are deliberately absent.
COVER = Entity('cover', 'cover', 'Garage Door')
WIRED_SENSOR = Entity('wired_sensor', 'binary_sensor', 'Wired Sensor',
                      legacy_ids=('garage_door_input',))
RANGE_SENSOR = Entity('range_sensor', 'binary_sensor',
                      'Garage Door Range Sensor',
                      legacy_ids=('garage_door_range_sensor',))
DISTANCE = Entity('distance', 'sensor', 'Sensor distance',
                  legacy_ids=('range_sensor',))
CALIBRATION = Entity('calibration', 'number', 'Sensor calibration',
                     legacy_ids=('open_garage_door_distance_from_ceiling',))
STR_OUTPUT = Entity('str_output', 'switch', 'STR output',
                    legacy_ids=('output_switch',))
PRE_CLOSE_WARNING = Entity('pre_close_warning', 'button',
                           'Pre-close Warning')
RESTART = Entity('restart', 'button', 'Restart',
                 legacy_ids=('restart_button',))
WIFI_SIGNAL = Entity('wifi_signal', 'sensor', 'WiFi Signal',
                     legacy_ids=('wifi_signal_db',))
WIFI_SIGNAL_PCT = Entity('wifi_signal_pct', 'sensor', 'WiFi Signal %',
                         legacy_ids=('wifi_signal_pct',))
UPTIME = Entity('uptime', 'sensor', 'Uptime', legacy_ids=('uptime_sensor',))
DEVICE_ID = Entity('device_id', 'text_sensor', 'Device ID')
IP_ADDRESS = Entity('ip_address', 'text_sensor', 'IP Address')
ESPHOME_VERSION = Entity('esphome_version', 'text_sensor', 'ESPHome Version')
PROJECT_VERSION = Entity('project_version', 'text_sensor',
                         'Project version')

ALL = [COVER, WIRED_SENSOR, RANGE_SENSOR, DISTANCE, CALIBRATION, STR_OUTPUT,
       PRE_CLOSE_WARNING, RESTART, WIFI_SIGNAL, WIFI_SIGNAL_PCT, UPTIME,
       DEVICE_ID, IP_ADDRESS, ESPHOME_VERSION, PROJECT_VERSION]

_BY_SSE_ID = {}
for _ent in ALL:
    for _sid in _ent.sse_ids():
        _BY_SSE_ID[_sid.lower()] = _ent


def lookup(event):
    """Return the Entity an SSE event (or REST response) belongs to.

    Prefers the new-format ``name_id`` field when the firmware sends it,
    falls back to ``id`` (which is legacy-format on old firmware and
    new-format on firmware >= 2026.8).  Returns None for unknown ids.
    """
    for field in ('name_id', 'id'):
        value = event.get(field)
        if value:
            ent = _BY_SSE_ID.get(str(value).lower())
            if ent is not None:
                return ent
    return None
