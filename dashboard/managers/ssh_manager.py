from .config_manager import ConfigManager
import subprocess
from typing import Tuple


def run_ssh_command(command: str) -> Tuple[bool, str, str]:
    config_manager = ConfigManager()
    ssh_host_config = config_manager.get_ssh_host_config()
    ssh_cmd_prefix = f'cd .. && export SSHPASS={ssh_host_config.get('password')} && sshpass -e ssh -o StrictHostKeyChecking=no {ssh_host_config.get('username')}@{ssh_host_config.get('endpoint')}'
    full_cmd = f'{ssh_cmd_prefix} "{command}"'
    result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
    return result.returncode == 0, result.stdout, result.stderr