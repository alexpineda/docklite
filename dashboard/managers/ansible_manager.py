import json
import os
import subprocess
from typing import Dict, List, Optional, Generator, Tuple
import yaml

from .file_paths import file_paths
from .config_manager import ConfigManager

from pathlib import Path

class AnsibleManager:
    def __init__(self):
        self.config_manager = ConfigManager()

    def prepare_service_vars(self, service_name: str, image: str, domain: str) -> dict:
        """Prepare service configuration with env vars and mount paths from config"""
        
        # Get env vars for service
        service_config = self.config_manager.get_services_config().get(service_name, {})
        env_vars = service_config.get('env_vars', {})
        
        # Get filesystem config
        filesystem_config = self.config_manager.get_raw_config().get('filesystem', {})
        host_path = filesystem_config.get('host_path')
        container_mount_path = filesystem_config.get('container_mount_path')
        
        return {
            'name': service_name,
            'image': image,
            'domain': domain,
            'env_vars': env_vars,
            'host_path': f"{host_path}/{service_name}",
            'container_mount_path': f"{container_mount_path}/{service_name}"
        }
        
    def prepare_services_vars(self, services: List[Dict[str, str]], write_to_file: bool = False) -> dict:
        # Prepare service configs with env vars
        service_configs = []
        for service in services:
            service_config = self.prepare_service_vars(
                service_name=service['name'],
                image=service['image'],
                domain=service['domain']
            )
            service_configs.append(service_config)
        
        # Create temporary vars file for all services
        service_vars = {'api_services': service_configs}

        temp_vars_file = os.path.join(file_paths['ansible_dir'], 'vars', 'services_to_deploy.yml')
        if write_to_file:
            with open(temp_vars_file, 'w') as f:
                yaml.dump(service_vars, f)

        return service_vars

    def _update_domain_yaml(self):
        """Update domain.yml with current configuration"""
        config = {
            'base_domain': self.config_manager.get_caddy_config().get('base_domain'),
            'caddy_email': self.config_manager.get_caddy_config().get('email')
        }
    
        with open(file_paths['domain_yml'], 'w') as f:
            yaml.dump(config, f, default_flow_style=False)

    def setup_deployment(self) -> Tuple[str, List[str]]:
        """Setup deployment environment and return (docker_config_path, cleanup_files)"""
        docker_config = file_paths['docker_config']
        cleanup_files = []
        
        # Create inventory file
        inventory_path = file_paths['inventory_yml']

        ssh_host_config = self.config_manager.get_ssh_host_config()

        with open(inventory_path, 'w') as f:
            f.write(f"""all:
  hosts:
    api_server:
      ansible_host: {ssh_host_config['endpoint']}
      ansible_user: {ssh_host_config['username']}
      ansible_password: {ssh_host_config['password']}
      ansible_become_password: {ssh_host_config['password']}
      docker_config_file: {docker_config}
""")
        cleanup_files.append(inventory_path)

        # print the inventory file
        with open(inventory_path, 'r') as f:
            print(f.read())

                    
        # Update domain configuration
        self._update_domain_yaml()
        
        return cleanup_files
        
    def run_playbook(self, playbooks: str | list[str], extra_vars: Optional[str] = None) -> Generator[str, None, bool]:
        """Run one or more Ansible playbooks and yield output
        
        Args:
            playbooks: Either a single playbook string or list of playbook strings
            extra_vars: Extra vars to apply to all playbooks
        """
        inventory_path = file_paths['inventory_yml'] 
        
        # Convert single playbook to list format
        if isinstance(playbooks, str):
            playbooks = [playbooks]
            
        # Build command with all playbooks
        cmd = ['ansible-playbook', '-i', inventory_path]
        
        # Add playbook paths
        for playbook in playbooks:
            playbook_path = os.path.join(file_paths['ansible_dir'], playbook)
            cmd.append(playbook_path)
            
        if extra_vars:
            cmd.extend(['-e', extra_vars])
            
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        success = False
        
        while True:
            output = process.stdout.readline()
            if output:
                yield f"data: {output}\n\n"
                if "failed=0" in output and "unreachable=0" in output:
                    success = True
            if process.poll() is not None:
                break
                
        return success
