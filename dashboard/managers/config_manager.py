import json
import os

from .file_paths import file_paths


class ConfigManager:
    def __init__(self):
        self.config = self._load_config()

    def _load_config(self):
        with open(file_paths['config_json']) as f:
            return json.load(f)

    def get_raw_config(self):
        return self.config

    def get_ssh_host_config(self):
        return self.config.get('ssh_host', {})

    def get_registry_config(self):
        return self.config.get('registry', {})

    def get_services_config(self):
        return self.config.get('services', {})
    
    def get_caddy_config(self):
        return self.config.get('caddy', {})

    def get_caddy_custom_directives(self):
        return self.config.get('caddy', {}).get('custom_directives', [])