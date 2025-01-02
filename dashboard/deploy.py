#!/usr/bin/env python3
import os
import sys
import yaml
import subprocess
from dotenv import load_dotenv

def load_env():
    load_dotenv()
    # Get the dashboard directory path
    dashboard_dir = os.path.dirname(os.path.abspath(__file__))
    docker_config = os.path.join(dashboard_dir, 'docker-config.json')
    
    return {
        'BASE_DOMAIN': os.getenv('BASE_DOMAIN', 'default.com'),
        'DROPLET_IP': os.getenv('DROPLET_IP'),
        'DROPLET_USER': os.getenv('HOST_SSH_USER', 'root'),
        'DROPLET_PASSWORD': os.getenv('HOST_SSH_PASSWORD'),
        'DROPLET_SSH_KEY': os.getenv('DROPLET_SSH_KEY', '~/.ssh/id_rsa'),
        'CADDY_EMAIL': os.getenv('CADDY_EMAIL', 'alexpineda@fastmail.com'),
        'DOCKER_CONFIG': docker_config
    }

def get_subdomain_from_image(image):
    """Extract subdomain from image name
    Example: registry.digitalocean.com/api-alexpineda-containers/my-api:latest -> my-api
    """
    # Split on / and take last part before :
    parts = image.split('/')
    name = parts[-1].split(':')[0]
    return name

def ensure_latest_tag(image):
    """Ensure image has :latest tag if no tag specified"""
    if ':' not in image:
        return f"{image}:latest"
    return image

def update_domain_yaml(env):
    """Update domain.yml with current configuration"""
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    domain_path = os.path.join(parent_dir, 'dashboard', 'ansible', 'vars', 'domain.yml')
    
    config = {
        'base_domain': env['BASE_DOMAIN'],
        'caddy_email': env['CADDY_EMAIL']
    }
    
    with open(domain_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False)

def run_ansible(env, full_redeploy=False):
    """Run Ansible playbook for deployment"""
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ansible_dir = os.path.join(parent_dir, 'dashboard', 'ansible')
    
    # Change to ansible directory
    os.chdir(ansible_dir)
    
    # Create inventory file
    inventory_path = os.path.join(ansible_dir, 'inventory.yml')
    with open(inventory_path, 'w') as f:
        f.write(f"""all:
  hosts:
    api_server:
      ansible_host: {env['DROPLET_IP']}
      ansible_user: {env['DROPLET_USER']}
      ansible_password: {env['DROPLET_PASSWORD']}
      ansible_become_password: {env['DROPLET_PASSWORD']}
      docker_config_file: {env['DOCKER_CONFIG']}
""")
    
    try:
        # Run playbook
        playbook = 'playbook.yml' if full_redeploy else 'deploy.yml'
        playbook_path = os.path.join(ansible_dir, playbook)
        subprocess.run(['ansible-playbook', '-i', inventory_path, playbook_path], check=True)
        return True
    finally:
        # Clean up inventory file
        if os.path.exists(inventory_path):
            os.remove(inventory_path)

def check_container_status(env, container_name):
    """Check Docker container status and logs"""
    try:
        # Check container status
        status_cmd = f"docker ps -a --filter name={container_name} --format '{{{{.Status}}}}'"
        status = subprocess.run(['ssh', f"{env['DROPLET_USER']}@{env['DROPLET_IP']}", status_cmd], 
                              capture_output=True, text=True, check=True).stdout.strip()
        
        # Get recent logs
        logs_cmd = f"docker logs --tail 50 {container_name} 2>&1"
        logs = subprocess.run(['ssh', f"{env['DROPLET_USER']}@{env['DROPLET_IP']}", logs_cmd],
                            capture_output=True, text=True, check=True).stdout.strip()
        
        print(f"\nContainer Status: {status}")
        print("\nRecent Logs:")
        print(logs)
        
        return "Up" in status
    except subprocess.CalledProcessError as e:
        print(f"\nError checking container: {str(e)}")
        if e.stderr:
            print(f"Error output: {e.stderr}")
        return False

def main():
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("Usage: ./deploy.py <docker-image> [--full-redeploy]")
        print("Example: ./deploy.py registry.digitalocean.com/api-alexpineda-containers/my-api:latest --full-redeploy")
        sys.exit(1)
    
    image = ensure_latest_tag(sys.argv[1])
    full_redeploy = '--full-redeploy' in sys.argv
    env = load_env()
    container_name = get_subdomain_from_image(image)
    
    try:
        update_domain_yaml(env)
        if not run_ansible(env, full_redeploy):
            print("Service deployment failed")
            sys.exit(1)
            
        print("\nChecking container status...")
        if not check_container_status(env, container_name):
            print("Service deployment failed - container is not running properly")
            sys.exit(1)
            
        print("Service deployment successful!")
    except Exception as e:
        print(f"Service deployment failed: {str(e)}")
        sys.exit(1)

if __name__ == '__main__':
    main() 