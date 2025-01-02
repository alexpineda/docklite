import os
import re
import subprocess
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import json

class CaddyManager:
    def __init__(self):
        self.ssh_cmd_prefix = 'cd .. && source .env && export SSHPASS=$HOST_SSH_PASSWORD && sshpass -e ssh -o StrictHostKeyChecking=no $HOST_SSH_USER@$DROPLET_IP'
        self.caddy_dir = '/etc/caddy'
        self.conf_d_dir = f'{self.caddy_dir}/conf.d'

    def _run_ssh_command(self, cmd: str) -> Tuple[bool, str, str]:
        """Run a command over SSH and return success, stdout, stderr"""
        full_cmd = f'{self.ssh_cmd_prefix} "{cmd}"'
        result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
        return result.returncode == 0, result.stdout, result.stderr

    def get_main_config(self) -> Tuple[bool, str, str]:
        """Get the contents of the main Caddyfile"""
        return self._run_ssh_command(f'cat {self.caddy_dir}/Caddyfile')

    def get_conf_d_files(self) -> Tuple[bool, List[str], str]:
        """Get list of files in conf.d directory"""
        success, stdout, stderr = self._run_ssh_command(f'ls -1 {self.conf_d_dir}')
        files = stdout.strip().split('\n') if stdout.strip() else []
        return success, files, stderr

    def get_conf_d_file_content(self, filename: str) -> Tuple[bool, str, str]:
        """Get contents of a specific conf.d file"""
        return self._run_ssh_command(f'cat {self.conf_d_dir}/{filename}')

    def get_full_config(self, active_domains: set) -> Dict:
        """
        Get full Caddy configuration and identify stale entries
        Returns a dict with 'config' string and 'stale_files' dict
        """
        try:
            # Get main Caddyfile
            main_success, main_content, main_error = self.get_main_config()
            if not main_success:
                return {'error': f'Failed to fetch Caddy config: {main_error}'}

            # Load custom directives from config
            try:
                with open('config.json', 'r') as f:
                    config = json.load(f)
                    custom_directives = config.get('caddy', {}).get('custom_directives', [])
            except Exception:
                custom_directives = []

            config = "# Main Caddyfile\n"
            config += main_content + "\n\n"

            # Add custom directives if any
            if custom_directives:
                config += "# Custom Directives\n"
                config += "\n".join(custom_directives) + "\n\n"
            
            # Get conf.d contents
            success, files, error = self.get_conf_d_files()
            stale_files = {}
            
            if success and files:
                config += "# Contents of /etc/caddy/conf.d/\n"
                
                for file in files:
                    success, content, error = self.get_conf_d_file_content(file)
                    if success:
                        # Parse domains in this file
                        domains = re.findall(r'^([^\s{]+)\s*{', content, re.MULTILINE)
                        if set(domains).isdisjoint(active_domains):  # All domains in file are stale
                            stale_files[file] = domains
                            config += f"\n### STALE CONFIGURATION ({file}) ###\n{content}\n### END STALE CONFIGURATION ###\n"
                        else:
                            config += f"\n# {file}\n{content}\n"
            else:
                config += "# No configurations found in /etc/caddy/conf.d/ or directory is empty\n"
                
            return {
                'config': config,
                'stale_files': stale_files
            }
            
        except Exception as e:
            return {'error': str(e)}

    def cleanup_stale_configs(self, stale_files: List[str]) -> Dict:
        """
        Move stale configuration files to backup directory
        Returns a dict with 'message' or 'error'
        """
        try:
            if not stale_files:
                return {'message': 'No stale configurations found'}
                
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_dir = f'conf.d.backup_{timestamp}'
            files_list = ' '.join(f'{self.conf_d_dir}/{f}' for f in stale_files)
            
            cmd = f'cd {self.caddy_dir} && sudo mkdir -p {backup_dir} && sudo mv {files_list} {backup_dir}/ && sudo systemctl reload caddy'
            success, stdout, stderr = self._run_ssh_command(cmd)
            
            if success:
                return {
                    'message': f'Successfully moved {len(stale_files)} stale configuration(s) to {backup_dir}',
                    'moved_files': stale_files
                }
            else:
                return {'error': f'Failed to cleanup Caddy config: {stderr}'}
                
        except Exception as e:
            return {'error': str(e)}

    def reload_caddy(self) -> Tuple[bool, str]:
        """Reload Caddy service"""
        success, stdout, stderr = self._run_ssh_command('sudo systemctl reload caddy')
        return success, stderr if not success else "Caddy reloaded successfully" 

    def test_config(self) -> Dict:
        """Test Caddy configuration using caddy validate"""
        try:
            cmd = f'sudo caddy validate --config {self.caddy_dir}/Caddyfile'
            success, stdout, stderr = self._run_ssh_command(cmd)
            
            if success:
                return {'success': True, 'message': 'Configuration is valid'}
            else:
                return {'success': False, 'message': f'Configuration validation failed: {stderr}'}
                
        except Exception as e:
            return {'success': False, 'message': str(e)} 