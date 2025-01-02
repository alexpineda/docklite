from flask import Flask, render_template, request, redirect, url_for, flash, Response, stream_with_context, jsonify
import docker
import yaml
import os
import subprocess
from docker.errors import DockerException
from dotenv import load_dotenv
from datetime import datetime
import json
from typing import Optional, Dict, List, Generator, Any, Tuple

# Load environment variables
load_dotenv()

class DockerManager:
    def __init__(self):
        self.client = self._setup_docker_client()
        
    def _setup_docker_client(self) -> Optional[docker.DockerClient]:
        """Setup Docker client with TLS if configured"""
        docker_host = os.getenv('DOCKER_HOST', f"tcp://{os.getenv('DROPLET_IP', 'localhost')}:2376")
        cert_locations = [
            os.path.join(os.path.dirname(__file__), 'ansible', 'certs'),
            os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'certs')),
            os.path.expanduser('~/.docker/machine/certs'),
        ]
        docker_cert_path = next((path for path in cert_locations if os.path.exists(path)), cert_locations[0])
        
        docker_kwargs = {}
        if os.getenv('DOCKER_TLS_VERIFY', '1') == '1':
            cert_path = os.path.join(docker_cert_path, 'cert.pem')
            key_path = os.path.join(docker_cert_path, 'key.pem')
            ca_path = os.path.join(docker_cert_path, 'ca.pem')
            
            if not all(os.path.exists(p) for p in [cert_path, key_path, ca_path]):
                print(f"Warning: Missing certificates in {docker_cert_path}")
                return None
                
            docker_kwargs = {
                'base_url': docker_host,
                'tls': docker.tls.TLSConfig(
                    client_cert=(cert_path, key_path),
                    ca_cert=ca_path,
                    verify=True
                )
            }
        else:
            docker_kwargs = {'base_url': docker_host}
            
        try:
            client = docker.DockerClient(**docker_kwargs)
            client.ping()
            return client
        except DockerException as e:
            print(f"Docker not available: {e}")
            return None
            
    def get_container_stats(self, name: str) -> Dict:
        """Get container statistics"""
        if not self.client:
            raise Exception('Docker not available')
            
        container = self.client.containers.get(name)
        if container.status != 'running':
            raise Exception('Container not running')
            
        stats = container.stats(stream=False)
        
        # Calculate CPU percentage
        cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - stats['precpu_stats']['cpu_usage']['total_usage']
        system_delta = stats['cpu_stats']['system_cpu_usage'] - stats['precpu_stats']['system_cpu_usage']
        
        num_cpus = 1
        if 'percpu_usage' in stats['cpu_stats']['cpu_usage']:
            num_cpus = len(stats['cpu_stats']['cpu_usage']['percpu_usage'])
        elif 'online_cpus' in stats['cpu_stats']:
            num_cpus = stats['cpu_stats']['online_cpus']
            
        cpu_percent = 0.0
        if system_delta > 0:
            cpu_percent = (cpu_delta / system_delta) * num_cpus * 100.0
            
        mem_usage = stats['memory_stats']['usage']
        mem_limit = stats['memory_stats']['limit']
        mem_percent = (mem_usage / mem_limit) * 100.0
        
        inspect = container.attrs
        started_at = datetime.fromisoformat(inspect['State']['StartedAt'].replace('Z', '+00:00'))
        uptime = datetime.now(started_at.tzinfo) - started_at
        
        return {
            'cpu_percent': round(cpu_percent, 2),
            'memory_usage': round(mem_usage / (1024 * 1024), 2),
            'memory_limit': round(mem_limit / (1024 * 1024), 2),
            'memory_percent': round(mem_percent, 2),
            'uptime_seconds': uptime.total_seconds(),
            'uptime_human': str(uptime).split('.')[0],
        }

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
        
    def run_playbook(self, playbook: str, extra_vars: Optional[str] = None) -> Generator[str, None, bool]:
        """Run Ansible playbook and yield output"""
        inventory_path = os.path.join(self.ansible_dir, 'inventory.yml')
        playbook_path = os.path.join(self.ansible_dir, playbook)
        cmd = ['ansible-playbook', '-i', inventory_path, playbook_path]
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

class RegistryManager:
    def __init__(self):
        self.registry_url = os.getenv('REGISTRY_URL', 'registry.digitalocean.com')
        self.registry_namespace = os.getenv('REGISTRY_NAMESPACE', 'api-alexpineda-containers')
        
    def list_images(self) -> List[Dict[str, str]]:
        """List all images in the registry namespace"""
        try:
            result = subprocess.run(['doctl', 'registry', 'repository', 'list-v2'], 
                                  capture_output=True, text=True, check=True)
            
            repositories = []
            for line in result.stdout.strip().split('\n')[1:]:
                if line.strip():
                    repo_name = line.split()[0]
                    repositories.append({'name': repo_name})
            
            services = []
            for repo in repositories:
                try:
                    tags_result = subprocess.run(
                        ['doctl', 'registry', 'repository', 'list-tags', repo['name']],
                        capture_output=True, text=True, check=True
                    )
                    
                    tags = []
                    for line in tags_result.stdout.strip().split('\n')[1:]:
                        if line.strip():
                            tag_name = line.split()[0]
                            tags.append({'tag': tag_name})
                    
                    if tags:
                        tag = tags[0]['tag']
                        image = f"{self.registry_url}/{self.registry_namespace}/{repo['name']}:{tag}"
                        name = repo['name']
                        services.append({
                            'name': name,
                            'image': image,
                            'domain': f"{name}.{os.getenv('BASE_DOMAIN')}"
                        })
                except Exception as e:
                    print(f"Error getting tags for repository {repo['name']}: {e}")
                    continue
            
            return services
        except Exception as e:
            print(f"Error listing registry images: {e}")
            return []

# Initialize managers
docker_manager = DockerManager()
ansible_manager = AnsibleManager()
registry_manager = RegistryManager()

app = Flask(__name__)
app.secret_key = os.urandom(24)

@app.route('/')
def dashboard():
    docker_available = docker_manager.client is not None
    services = []
    orphaned_containers = []
    error_message = None
    
    if not docker_available:
        error_message = "Docker is not available. Please check server setup and configuration."
    else:
        try:
            services = registry_manager.list_images()
            containers = docker_manager.client.containers.list(all=True)
            matched_containers = set()
            
            for service in services:
                container = next((c for c in containers if c.name == service['name']), None)
                if container:
                    matched_containers.add(container.id)
                    service.update({
                        'status': container.status,
                        'running': container.status == 'running',
                        'logs': container.logs(tail=5).decode('utf-8').split('\n'),
                        'deployed': True,
                        'image_mismatch': False  # Simplified for brevity
                    })
                else:
                    service.update({
                        'status': 'not deployed',
                        'running': False,
                        'logs': [],
                        'deployed': False,
                        'image_mismatch': False
                    })
                    
            for container in containers:
                if container.id not in matched_containers:
                    orphaned_containers.append({
                        'name': container.name,
                        'image': container.image.tags[0] if container.image.tags else 'unknown',
                        'status': container.status,
                        'running': container.status == 'running',
                        'logs': container.logs(tail=5).decode('utf-8').split('\n')
                    })
                    
        except Exception as e:
            error_message = f"Error connecting to Docker: {e}"
            print(error_message)
            
    return render_template('dashboard.html',
                         services=services,
                         orphaned_containers=orphaned_containers,
                         docker_available=docker_available,
                         error_message=error_message,
                         base_domain=os.getenv('BASE_DOMAIN', 'alexpineda.ca'))

def stream_deployment(playbook: str, extra_vars: Optional[str] = None) -> Generator[str, None, None]:
    """Generic deployment streaming function"""
    try:
        docker_config, cleanup_files = ansible_manager.setup_deployment()
        
        if not os.path.exists(docker_config):
            yield f"data: Error: Docker config file not found at {docker_config}\n\n"
            return
            
        try:
            # Update domain configuration
            from deploy import update_domain_yaml
            update_domain_yaml(ansible_manager.env)
            yield "data: Updated domain configuration\n\n"
        except Exception as e:
            yield f"data: Warning: Could not update domain configuration: {str(e)}\n\n"
            
        try:
            yield f"data: Starting deployment with {playbook}...\n\n"
            success = False
            for output in ansible_manager.run_playbook(playbook, extra_vars):
                yield output
                
            if success:
                yield "data: Deployment completed successfully\n\n"
            else:
                yield "data: Deployment failed\n\n"
                
        finally:
            for file in cleanup_files:
                if os.path.exists(file):
                    os.remove(file)
                    
    except Exception as e:
        yield f"data: Error: {str(e)}\n\n"

@app.route('/stream-deploy')
def stream_deploy():
    """Stream full server redeploy"""
    return Response(
        stream_with_context(stream_deployment('playbook.yml')),
        mimetype='text/event-stream'
    )

@app.route('/stream-deploy-service')
def stream_deploy_service():
    """Stream single service deployment"""
    service_name = request.args.get('name')
    if not service_name:
        return Response("data: Error: No service name provided\n\n", mimetype='text/event-stream')
        
    services = registry_manager.list_images()
    service = next((s for s in services if s['name'] == service_name), None)
    
    if not service:
        return Response("data: Error: Service not found in registry\n\n", mimetype='text/event-stream')
        
    # Create temporary vars file for this service
    service_vars = {'api_services': [service]}
    temp_vars_file = os.path.join(ansible_manager.ansible_dir, 'vars', 'temp_service.yml')
    
    # Ensure vars directory exists
    os.makedirs(os.path.dirname(temp_vars_file), exist_ok=True)
    
    with open(temp_vars_file, 'w') as f:
        yaml.dump(service_vars, f)
        
    return Response(
        stream_with_context(stream_deployment('deploy.yml', f'@{temp_vars_file}')),
        mimetype='text/event-stream'
    )

@app.route('/container-stats/<name>')
def container_stats(name):
    try:
        stats = docker_manager.get_container_stats(name)
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/container-logs/<name>')
def container_logs(name):
    if not docker_manager.client:
        return jsonify({'error': 'Docker not available'}), 500
        
    try:
        container = docker_manager.client.containers.get(name)
        status = container.status
        logs = container.logs(tail=100, timestamps=True).decode('utf-8')
        
        inspect = container.attrs
        status_details = {
            'status': status,
            'state': inspect['State'],
            'platform': inspect['Platform'],
            'created': inspect['Created'],
            'started_at': inspect['State'].get('StartedAt'),
            'health': inspect['State'].get('Health', {}).get('Status', 'N/A')
        }
        
        return jsonify({
            'status': f"Status: {status}\nHealth: {status_details['health']}\nStarted: {status_details['started_at']}\nPlatform: {status_details['platform']}",
            'logs': logs
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/caddy-config')
def caddy_config():
    """Fetch the actual Caddy configuration from the server and identify stale entries"""
    try:
        # Get current services for comparison
        services = registry_manager.list_images()
        active_domains = {service['domain'] for service in services}
        
        # Get main Caddyfile
        main_cmd = ['source .env && export SSHPASS=$HOST_SSH_PASSWORD && sshpass -e ssh -o StrictHostKeyChecking=no $HOST_SSH_USER@$DROPLET_IP "cat /etc/caddy/Caddyfile"']
        main_result = subprocess.run(main_cmd, shell=True, capture_output=True, text=True)
        
        # List files in conf.d and get their contents
        list_cmd = ['source .env && export SSHPASS=$HOST_SSH_PASSWORD && sshpass -e ssh -o StrictHostKeyChecking=no $HOST_SSH_USER@$DROPLET_IP "ls -1 /etc/caddy/conf.d/"']
        list_result = subprocess.run(list_cmd, shell=True, capture_output=True, text=True)
        
        if main_result.returncode == 0:
            config = "# Main Caddyfile\n"
            config += main_result.stdout + "\n\n"
            
            stale_files = {}
            if list_result.returncode == 0 and list_result.stdout.strip():
                config += "# Contents of /etc/caddy/conf.d/\n"
                files = list_result.stdout.strip().split('\n')
                
                for file in files:
                    cat_cmd = [f'source .env && export SSHPASS=$HOST_SSH_PASSWORD && sshpass -e ssh -o StrictHostKeyChecking=no $HOST_SSH_USER@$DROPLET_IP "cat /etc/caddy/conf.d/{file}"']
                    cat_result = subprocess.run(cat_cmd, shell=True, capture_output=True, text=True)
                    if cat_result.returncode == 0:
                        content = cat_result.stdout
                        # Parse domains in this file
                        import re
                        domains = re.findall(r'^([^\s{]+)\s*{', content, re.MULTILINE)
                        if set(domains).isdisjoint(active_domains):  # All domains in file are stale
                            stale_files[file] = domains
                            config += f"\n### STALE CONFIGURATION ({file}) ###\n{content}\n### END STALE CONFIGURATION ###\n"
                        else:
                            config += f"\n# {file}\n{content}\n"
            else:
                config += "# No configurations found in /etc/caddy/conf.d/ or directory is empty\n"
                
            return jsonify({
                'config': config,
                'stale_files': stale_files
            })
        else:
            return jsonify({'error': f'Failed to fetch Caddy config: {main_result.stderr}'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/cleanup-caddy-config', methods=['POST'])
def cleanup_caddy_config():
    """Move stale configuration files to backup directory"""
    try:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_dir = f'conf.d.backup_{timestamp}'
        
        # First, get the list of stale files
        response = caddy_config()
        if response.status_code != 200:
            return response
            
        data = response.json
        if not data.get('stale_files'):
            return jsonify({'message': 'No stale configurations found'})
            
        stale_files = list(data['stale_files'].keys())
        files_list = ' '.join(f'/etc/caddy/conf.d/{f}' for f in stale_files)
        
        backup_cmd = [
            'source .env && export SSHPASS=$HOST_SSH_PASSWORD && '
            'sshpass -e ssh -o StrictHostKeyChecking=no $HOST_SSH_USER@$DROPLET_IP '
            f'"cd /etc/caddy && '
            f'sudo mkdir -p {backup_dir} && '
            f'sudo mv {files_list} {backup_dir}/ && '
            f'sudo systemctl reload caddy"'
        ]
        result = subprocess.run(backup_cmd, shell=True, capture_output=True, text=True)
        
        if result.returncode == 0:
            return jsonify({
                'message': f'Successfully moved {len(stale_files)} stale configuration(s) to {backup_dir}',
                'moved_files': stale_files
            })
        else:
            return jsonify({'error': f'Failed to cleanup Caddy config: {result.stderr}'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/redeploy-all', methods=['POST'])
def redeploy_all():
    if not docker_manager.client:
        flash('Docker is not available. Please check server setup.', 'error')
        return redirect(url_for('dashboard'))
        
    return render_template('deploy_progress.html', 
                         title='Redeploying All Services',
                         message='Redeploying all services...')

@app.route('/service/<name>/restart', methods=['POST'])
def restart_service(name):
    if not docker_manager.client:
        flash('Docker is not available. Please check server setup.', 'error')
        return redirect(url_for('dashboard'))

    try:
        container = docker_manager.client.containers.get(name)
        container.restart()
        flash(f'Service {name} restarted successfully', 'success')
    except Exception as e:
        flash(f'Error restarting service: {str(e)}', 'error')
    return redirect(url_for('dashboard'))

@app.route('/service/<name>/shutdown', methods=['POST'])
def shutdown_service(name):
    if not docker_manager.client:
        flash('Docker is not available. Please check server setup.', 'error')
        return redirect(url_for('dashboard'))

    try:
        container = docker_manager.client.containers.get(name)
        container.stop()
        flash(f'Container {name} shut down successfully', 'success')
    except Exception as e:
        flash(f'Error shutting down container: {str(e)}', 'error')
    return redirect(url_for('dashboard'))

@app.route('/service/<name>/delete', methods=['POST'])
def delete_service(name):
    if not docker_manager.client:
        flash('Docker is not available. Please check server setup.', 'error')
        return redirect(url_for('dashboard'))

    try:
        container = docker_manager.client.containers.get(name)
        # Stop the container first if it's running
        if container.status == 'running':
            container.stop()
        # Remove the container
        container.remove()
        flash(f'Container {name} deleted successfully', 'success')
    except Exception as e:
        flash(f'Error deleting container: {str(e)}', 'error')
    return redirect(url_for('dashboard'))

@app.route('/stream-deploy-services')
def stream_deploy_services():
    """Stream the output of deploying all API services using deploy.yml"""
    return Response(
        stream_with_context(stream_deployment('deploy.yml')),
        mimetype='text/event-stream'
    )

if __name__ == '__main__':
    print(f"Connecting to Docker at {os.getenv('DOCKER_HOST')}")
    if not docker_manager.client:
        print("Warning: Docker is not available!")
    app.run(host='localhost', port=3000, debug=True) 