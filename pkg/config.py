"""Persistent configuration for the Konnected GDO White adapter."""
import logging
from gateway_addon import Database


class Config(Database):
    """Load the addon config from the gateway database."""

    def __init__(self, package_name):
        Database.__init__(self, package_name, None)
        self.log_level = 'WARNING'
        self.discover = True
        self.poll_interval = 30
        self.devices = []
        self.open()
        self.load()
        self.close()

    def load(self):
        config = {}
        try:
            config = self.load_config() or {}
            self.log_level = config.get('log_level', 'WARNING')
            self.discover = bool(config.get('discover', True))
            self.poll_interval = int(config.get('poll_interval', 30) or 30)
            if self.poll_interval < 5:
                self.poll_interval = 5
            self.devices = []
            for dev in config.get('devices', []) or []:
                host = str(dev.get('host', '')).strip()
                if not host:
                    continue
                port = int(dev.get('port', 80) or 80)
                self.devices.append({
                    'host': host,
                    'port': port,
                    'name': str(dev.get('name', '')).strip(),
                })
        except Exception as ex:
            logging.exception('Strange config: %s %s', ex, config)
