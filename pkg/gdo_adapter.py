"""Adapter for Konnected GDO White devices for WebThings Gateway."""

import logging
import re
import threading

from gateway_addon import Adapter

from .config import Config
from .gdo_device import GdoDevice
from . import mdns

PACKAGE = 'konnected-gdo-w'


def _id_from_mac(mac):
    return '{}-{}'.format(PACKAGE, mac.replace(':', '').lower())


def _id_from_host(host, port):
    slug = re.sub(r'[^a-z0-9]+', '-', host.lower()).strip('-')
    if port and int(port) != 80:
        slug = '{}-{}'.format(slug, port)
    return '{}-{}'.format(PACKAGE, slug)


class GdoAdapter(Adapter):
    """Adapter exposing Konnected GDO White garage door openers as Things."""

    def __init__(self, verbose=False):
        self.name = self.__class__.__name__
        Adapter.__init__(self, PACKAGE, PACKAGE, verbose=verbose)
        self._config = Config(self.package_name)
        self._apply_log_level()
        self._lock = threading.Lock()
        self._pairing = None

        for dev in self._config.devices:
            self._add_configured(dev)
        if self._config.discover:
            self.start_pairing(30)

    def _apply_log_level(self):
        levels = {'INFO': logging.INFO, 'DEBUG': logging.DEBUG}
        level = levels.get(self._config.log_level, logging.WARNING)
        logging.getLogger().setLevel(level)
        logging.info('Log level %s', self._config.log_level)

    # ---- adding devices ------------------------------------------------

    def _add_configured(self, dev):
        host, port = dev['host'], dev['port']
        _id = _id_from_host(host, port)
        if self.get_device(_id) is not None:
            return
        title = dev['name'] or 'Garage Door ({})'.format(host)
        device = GdoDevice(self, _id, host, port, title=title,
                           discovered=False,
                           poll_interval=self._config.poll_interval)
        self._register(device)

    def _add_discovered(self, svc):
        mac = str(svc.txt.get('mac', '')).replace(':', '').lower()
        if mac:
            _id = _id_from_mac(mac)
        else:
            _id = _id_from_host(svc.host, svc.port)
        with self._lock:
            for existing in self.get_devices().values():
                if existing.id == _id:
                    self._refresh_address(existing, svc)
                    return
                if mac and existing.mac == mac:
                    self._refresh_address(existing, svc)
                    return
                if existing.client.host in (svc.address, svc.target):
                    return
        title = svc.txt.get('friendly_name') or 'Garage Door'
        if mac:
            title = '{} {}'.format(title, mac[-6:])
        device = GdoDevice(self, _id, svc.host, svc.port or 80, title=title,
                           mac=mac or None, discovered=True,
                           poll_interval=self._config.poll_interval)
        self._register(device)

    def _register(self, device):
        logging.info('Adding device %s at %s', device.id,
                     device.client.base_url)
        self.handle_device_added(device)
        try:
            device.start()
        except Exception as ex:
            logging.exception('Starting %s failed: %s', device.id, ex)

    def _refresh_address(self, device, svc):
        new_host = svc.host
        new_port = svc.port or 80
        if new_host and (device.client.host != new_host or
                         device.client.port != new_port):
            logging.info('Device %s moved to %s:%s', device.id, new_host,
                         new_port)
            device.client.set_host(new_host, new_port)
            device.sync()

    # ---- discovery -----------------------------------------------------

    def start_pairing(self, timeout):
        """Run mDNS discovery in the background."""
        if not self._config.discover:
            logging.info('Discovery disabled in config')
            return
        if self._pairing is not None and self._pairing.is_alive():
            return
        self._pairing = threading.Thread(target=self._discover,
                                         name='gdo-discover')
        self._pairing.daemon = True
        self._pairing.start()

    def _discover(self):
        logging.debug('START discovery')
        try:
            for svc in mdns.find_gdo_devices():
                self._add_discovered(svc)
        except Exception as ex:
            logging.exception('Discovery failed: %s', ex)
        logging.debug('END discovery')

    def cancel_pairing(self):
        logging.debug('cancel_pairing')

    def rediscover(self, device):
        """Look for a quiet device again; it may have a new IP address."""
        for svc in mdns.find_gdo_devices():
            mac = str(svc.txt.get('mac', '')).replace(':', '').lower()
            if device.mac and mac == device.mac:
                self._refresh_address(device, svc)
                return True
        return False

    # ---- lifecycle -----------------------------------------------------

    def unload(self):
        logging.debug('Start unload all devices')
        for device in list(self.get_devices().values()):
            try:
                device.stop()
            except Exception as ex:
                logging.exception('Stopping %s failed: %s', device.id, ex)
        super().unload()
        logging.debug('End unload all devices')

    def handle_device_removed(self, device):
        logging.debug('Removing device %s', device.id)
        try:
            device.stop()
        except Exception as ex:
            logging.exception('Stopping %s failed: %s', device.id, ex)
        super().handle_device_removed(device)

    def remove_thing(self, device_id):
        device = self.get_device(device_id)
        if device is not None:
            self.handle_device_removed(device)
