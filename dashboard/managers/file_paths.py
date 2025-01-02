import os

_parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
print("_parent_dir:", _parent_dir)
_ansible_dir = os.path.join(_parent_dir, 'dashboard', 'ansible')
print("_ansible_dir:", _ansible_dir)
_dashboard_dir = os.path.join(_parent_dir, 'dashboard')
print("_dashboard_dir:", _dashboard_dir)

file_paths = {
    'ansible_dir':  os.path.abspath(_ansible_dir),
    'dashboard_dir': os.path.abspath(_dashboard_dir),
    'parent_dir': os.path.abspath(_parent_dir),
    'docker_config': os.path.abspath(os.path.join(_dashboard_dir, 'docker-config.json')),
    'domain_yml': os.path.abspath(os.path.join(_ansible_dir, 'vars', 'domain.yml')),
    'inventory_yml': os.path.abspath(os.path.join(_ansible_dir, 'inventory.yml')),
    'services_yml': os.path.abspath(os.path.join(_ansible_dir, 'vars', 'services.yml')),
    'config_json': os.path.abspath(os.path.join(_parent_dir, 'dashboard', 'config.json')),
}
