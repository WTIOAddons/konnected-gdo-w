# Konnected GDO White adapter

WebThings Gateway add-on for the [Konnected Garage Door Opener White](https://konnected.io/products/smart-garage-door-opener)
(models GDOv2-S and GDOv1-S). The GDO White runs ESPHome firmware and this
adapter talks to it over the device's local REST API and server-sent event
stream. No cloud account and no Home Assistant are required.

Companion to the [konnected-adapter](https://github.com/WTIOAddons/konnected-adapter)
add-on, which covers the Konnected alarm panels.

## How it works

* **Discovery.** The device advertises `_konnected._tcp` over mDNS. The
  adapter sends one multicast query and adds every device whose
  `project_name` is a GDO White. Devices can also be added by IP address or
  hostname in the add-on configuration.
* **Live state.** The adapter keeps a connection to the device's `/events`
  stream. Door, sensor and output changes appear in the gateway within a
  second of happening. If the stream drops it reconnects with back-off and
  falls back to polling the REST API.
* **Control.** Open, close, stop and toggle are `POST`s to the device's
  garage door cover entity. The device's own firmware handles the pre-close
  warning beep and flash before closing.
* **Firmware compatibility.** ESPHome 2026.7 changed REST paths from
  `/cover/garage_door` to `/cover/Garage%20Door`. The adapter tries the
  current form first and falls back to the legacy form, and understands both
  the old and new event id formats, so it works with GDO firmware before
  and after the change.
* **Reconnection.** If a discovered device goes quiet (power loss, new
  DHCP lease) the adapter marks it disconnected, rediscovers it over mDNS
  and follows it to its new address.

## The Thing

Each GDO White appears as one Thing with `@type` `DoorSensor` and
`OnOffSwitch`.

### Properties

| Property | Type | Notes |
|---|---|---|
| `on` | boolean, OnOffProperty | Writable. On opens the door, off closes it. |
| `door` | boolean, OpenProperty | Read-only. True while the door is open. |
| `status` | string | `open`, `closed`, `opening`, `closing` or `unknown` |
| `wired_sensor` | boolean | Wired contact input, on = door open |
| `distance` | number (m) | Optical range sensor reading |
| `calibration` | number (m) | Writable. Distance measured when fully open. |
| `str_output` | boolean, OnOffProperty | Writable. The 12V STR output. |
| `wifi_signal` | number (dBm) | |
| `uptime` | integer (seconds) | |
| `ip_address` | string | |
| `firmware` | string | ESPHome version on the device |

### Actions

| Action | Effect |
|---|---|
| `open` | Open the door (device ignores it if already open) |
| `close` | Pre-close warning, then close (ignored if already closed) |
| `stop` | Stop the door if it is moving |
| `toggle` | Press the opener button |
| `preclose_warning` | Flash and beep without moving the door |
| `calibrate` | Store the current range reading as the fully-open distance. Run with the door open. |
| `restart` | Reboot the device |

### Events

`door_opened`, `door_closed`, `door_opening`, `door_closing`. Each carries
the new status string as data.

## Configuration

| Option | Default | Meaning |
|---|---|---|
| `log_level` | `WARNING` | `WARNING`, `INFO` or `DEBUG` |
| `discover` | `true` | Find devices with mDNS at start-up and when pairing |
| `poll_interval` | `30` | Seconds between REST health checks. The event stream is the primary source. |
| `devices` | `[]` | Manually added devices: `host` (IP or hostname), optional `port` and `name` |

Discovery needs the gateway and the GDO to be on the same network segment.
If your network blocks multicast, set `discover` to false and add the device
by IP address.

## Examples

Close the garage door at 22:00 if it is still open

`if DateTime is 22:00 and Garage Door is open, do Garage Door action "close"`

Turn on the driveway light when the door starts opening after dark

`if Garage Door event "Door opening" occurs and DateTime is Dark, turn Driveway Light on`

## Development

```
python3 -m venv venv
venv/bin/pip install "git+https://github.com/WebThingsIO/gateway-addon-python@v1.1.1#egg=gateway_addon" flake8
venv/bin/python -m flake8 . --max-line-length=79 --exclude=lib,venv
venv/bin/python -m unittest discover -s tests -t .
```

The tests run against a fake GDO web server (`tests/fake_gdo.py`) that
mimics the ESPHome REST and event stream endpoints, in both the current and
the legacy URL format.

To query the network for devices from the command line:

```
python3 -m pkg.mdns
```

## References

* [Konnected GDO White API reference](https://konnected.readme.io/reference/gdo-white-introduction)
* [konnected-esphome firmware and OpenAPI specs](https://github.com/konnected-io/konnected-esphome)
* [ESPHome web server REST API](https://esphome.io/web-api/)

## Release notes

0.1.0
 * Initial release.
