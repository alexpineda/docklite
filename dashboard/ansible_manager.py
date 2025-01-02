import os
import subprocess
from typing import Dict, List, Optional, Generator, Tuple

class AnsibleManager:
    def __init__(self):
        self.env = self._load_env()
        self.parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.dashboard_dir = os.path.dirname(os.path.abspath(__file__))
        self.ansible_dir = os.path.join(self.parent_dir, 'dashboard', 'ansible')
        
    def _load_env(self) -> Dict[str, str]:
        """Load environment variables needed for deployment"""
        return {
            'DROPLET_IP': os.getenv('DROPLET_IP'),
            'HOST_SSH_USER': os.getenv('HOST_SSH_USER', 'root'),
            'HOST_SSH_PASSWORD': os.getenv('HOST_SSH_PASSWORD'),
            'BASE_DOMAIN': os.getenv('BASE_DOMAIN', 'default.com'),
            'CADDY_EMAIL': os.getenv('CADDY_EMAIL', 'alexpineda@fastmail.com')
        }
        
    def setup_deployment(self) -> Tuple[str, List[str]]:
        """Setup deployment environment and return (docker_config_path, cleanup_files)"""
        os.chdir(self.ansible_dir)
        docker_config = os.path.join(self.dashboard_dir, 'docker-config.json')
        cleanup_files = []
        
        # Create inventory file
        inventory_path = os.path.join(self.ansible_dir, 'inventory.yml')
        with open(inventory_path, 'w') as f:
            f.write(f"""all:
  hosts:
    api_server:
      ansible_host: {self.env['DROPLET_IP']}
      ansible_user: {self.env['HOST_SSH_USER']}
      ansible_password: {self.env['HOST_SSH_PASSWORD']}
      ansible_become_password: {self.env['HOST_SSH_PASSWORD']}
      docker_config_file: {docker_config}
""")
        cleanup_files.append(inventory_path)
        
        return docker_config, cleanup_files
        
    def run_playbook(self, playbooks: str | list[str], extra_vars: Optional[str] = None) -> Generator[str, None, bool]:
        """Run one or more Ansible playbooks and yield output
        
        Args:
            playbooks: Either a single playbook string or list of playbook strings
            extra_vars: Extra vars to apply to all playbooks
        """
        inventory_path = os.path.join(self.ansible_dir, 'inventory.yml')
        
        # Convert single playbook to list format
        if isinstance(playbooks, str):
            playbooks = [playbooks]
            
        # Build command with all playbooks
        cmd = ['ansible-playbook', '-i', inventory_path]
        
        # Add playbook paths
        for playbook in playbooks:
            playbook_path = os.path.join(self.ansible_dir, playbook)
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
