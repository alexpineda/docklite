#!/usr/bin/env python3
import os
import sys
import yaml
import subprocess
from dotenv import load_dotenv

def load_env():
    load_dotenv()
    # Get the parent directory path (project root)
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    docker_config = os.path.join(parent_dir, 'docker-config.json')
    
    return {
        'BASE_DOMAIN': os.getenv('BASE_DOMAIN', 'default.com'),
        'DROPLET_IP': os.getenv('DROPLET_IP'),
        'DROPLET_USER': os.getenv('HOST_SSH_USER', 'root'),
        'DROPLET_PASSWORD': os.getenv('HOST_SSH_PASSWORD'),
        'DROPLET_SSH_KEY': os.getenv('DROPLET_SSH_KEY', '~/.ssh/id_rsa'),
        'CADDY_EMAIL': os.getenv('CADDY_EMAIL', 'alexpineda@fastmail.com'),
        'DOCKER_CONFIG': docker_config
    }

def update_services_yaml(image, subdomain, env):
    # Get the parent directory path
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    services_path = os.path.join(parent_dir, 'vars', 'services.yml')
    
    # Load existing config or create new one
    if os.path.exists(services_path):
        with open(services_path, 'r') as f:
            config = yaml.safe_load(f)
    else:
        config = {
            'api_services': []
        }
    
    # Create new service entry
    new_service = {
        'name': subdomain,
        'image': image,
        'domain': f"{subdomain}.{env['BASE_DOMAIN']}"
    }
    
    # Update or add service
    services = config.get('api_services', [])
    # Remove existing service with same name if it exists
    services = [s for s in services if s['name'] != subdomain]
    services.append(new_service)
    config['api_services'] = services
    
    # Write back to file
    with open(services_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False)

def update_domain_yaml(env):
    # Get the parent directory path
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    domain_path = os.path.join(parent_dir, 'vars', 'domain.yml')
    
    config = {
        'base_domain': env['BASE_DOMAIN'],
        'caddy_email': env['CADDY_EMAIL']
    }
    
    # Write to file
    with open(domain_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False)

def run_ansible(env):
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(parent_dir)
    
    # Expand docker config path
    docker_config = os.path.expanduser(env['DOCKER_CONFIG'])
    
    if env.get('DROPLET_PASSWORD'):
        # Create ansible config for password auth
        with open('ansible.cfg', 'w') as f:
            f.write(f"""[defaults]
host_key_checking = False
remote_user = {env['DROPLET_USER']}

[ssh_connection]
scp_if_ssh = True
""")
        
        # Create inventory file
        with open('inventory.yml', 'w') as f:
            f.write(f"""all:
  hosts:
    api_server:
      ansible_host: {env['DROPLET_IP']}
      ansible_user: {env['DROPLET_USER']}
      ansible_password: {env['DROPLET_PASSWORD']}
      ansible_become_password: {env['DROPLET_PASSWORD']}
      docker_config_file: {docker_config}
""")
        
        try:
            # Run ansible with password auth
            subprocess.run(['ansible-playbook', '-i', 'inventory.yml', 'playbook.yml'], check=True)
        finally:
            # Clean up sensitive files
            os.remove('inventory.yml')
            os.remove('ansible.cfg')
    else:
        # Create inventory file for SSH key auth
        with open('inventory.yml', 'w') as f:
            f.write(f"""all:
  hosts:
    api_server:
      ansible_host: {env['DROPLET_IP']}
      ansible_user: {env['DROPLET_USER']}
""")
        try:
            # Run with SSH key auth
            ssh_key = os.path.expanduser(env['DROPLET_SSH_KEY'])
            subprocess.run([
                'ansible-playbook',
                '-i', 'inventory.yml',
                '--private-key', ssh_key,
                'playbook.yml'
            ], check=True, env=dict(os.environ, ANSIBLE_HOST_KEY_CHECKING='False'))
        finally:
            # Clean up sensitive files
            os.remove('inventory.yml')

def main():
    if len(sys.argv) != 3:
        print("Usage: ./deploy.py <docker-image> <subdomain>")
        print("Example: ./deploy.py nginx:latest api")
        sys.exit(1)
    
    image = sys.argv[1]
    subdomain = sys.argv[2]
    env = load_env()
    
    try:
        update_domain_yaml(env)
        update_services_yaml(image, subdomain, env)
        run_ansible(env)
        print("Deployment successful!")
    except Exception as e:
        print(f"Deployment failed: {str(e)}")
        sys.exit(1)

if __name__ == '__main__':
    main() 