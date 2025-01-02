from flask import Flask, render_template, request, redirect, url_for, flash, Response, stream_with_context
import docker
import yaml
import os
import subprocess
from docker.errors import DockerException
from dotenv import load_dotenv
from datetime import datetime
import json

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)  # For flash messages

# Get Docker connection details from environment variables
DOCKER_HOST = os.getenv('DOCKER_HOST', f"tcp://{os.getenv('DROPLET_IP', 'localhost')}:2376")
REGISTRY_URL = os.getenv('REGISTRY_URL', 'registry.digitalocean.com')
REGISTRY_NAMESPACE = os.getenv('REGISTRY_NAMESPACE', 'api-alexpineda-containers')

# Define possible certificate locations, prioritizing dashboard directory
CERT_LOCATIONS = [
    os.path.join(os.path.dirname(__file__), 'ansible', 'certs'),  # Dashboard certs directory (preferred)
    os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'certs')),  # Project root certs
    os.path.expanduser('~/.docker/machine/certs'),  # Docker Machine certs directory
]

# Find first existing certificate directory
DOCKER_CERT_PATH = next((path for path in CERT_LOCATIONS if os.path.exists(path)), CERT_LOCATIONS[0])
DOCKER_TLS_VERIFY = os.getenv('DOCKER_TLS_VERIFY', '1')

# Configure Docker client with better error handling
docker_kwargs = {}
if DOCKER_TLS_VERIFY == '1':
    cert_path = os.path.join(DOCKER_CERT_PATH, 'cert.pem')
    key_path = os.path.join(DOCKER_CERT_PATH, 'key.pem')
    ca_path = os.path.join(DOCKER_CERT_PATH, 'ca.pem')
    
    if not all(os.path.exists(p) for p in [cert_path, key_path, ca_path]):
        print(f"Warning: Missing certificates in {DOCKER_CERT_PATH}")
        print(f"Cert exists: {os.path.exists(cert_path)}")
        print(f"Key exists: {os.path.exists(key_path)}")
        print(f"CA exists: {os.path.exists(ca_path)}")
    
    docker_kwargs = {
        'base_url': DOCKER_HOST,
        'tls': docker.tls.TLSConfig(
            client_cert=(cert_path, key_path),
            ca_cert=ca_path,
            verify=True
        )
    }
else:
    docker_kwargs = {'base_url': DOCKER_HOST}

def check_docker():
    try:
        client = docker.DockerClient(**docker_kwargs)
        client.ping()
        return client
    except DockerException as e:
        print(f"Docker not available: {e}")
        return None

client = check_docker()

def get_registry_auth():
    """Get registry authentication from docker config"""
    try:
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'docker-config.json')
        with open(config_path) as f:
            config = json.load(f)
            # Get the raw auth token - this is already base64 encoded
            auth = config['auths'][REGISTRY_URL]['auth']
            print(f"Found auth token in config")
            return auth
    except Exception as e:
        print(f"Error loading registry auth: {e}")
        return None

def list_registry_images():
    """List all images in the registry namespace using doctl CLI"""
    try:
        # Get repositories using doctl
        print("Fetching repositories using doctl...")
        import subprocess
        result = subprocess.run(['doctl', 'registry', 'repository', 'list-v2'], 
                              capture_output=True, text=True, check=True)
        
        # Parse the line-delimited output
        repositories = []
        for line in result.stdout.strip().split('\n')[1:]:  # Skip header row
            if line.strip():
                # Split by whitespace and get the repository name (first column)
                repo_name = line.split()[0]
                repositories.append({'name': repo_name})
        
        print(f"Raw repositories: {repositories}")
        
        services = []
        for repo in repositories:
            try:
                # Get tags for repository
                tags_result = subprocess.run(
                    ['doctl', 'registry', 'repository', 'list-tags', repo['name']],
                    capture_output=True, text=True, check=True
                )
                
                # Parse the line-delimited output for tags
                tags = []
                for line in tags_result.stdout.strip().split('\n')[1:]:  # Skip header row
                    if line.strip():
                        # Split by whitespace and get the tag name (first column)
                        tag_name = line.split()[0]
                        tags.append({'tag': tag_name})
                
                print(f"Tags for {repo['name']}: {tags}")
                
                if tags:
                    # Use latest tag or first available
                    tag = next((t['tag'] for t in tags if t['tag'] == 'latest'), tags[0]['tag'])
                    # Properly construct image URL with namespace
                    image = f"{REGISTRY_URL}/{REGISTRY_NAMESPACE}/{repo['name']}:{tag}"
                    # Use repository name as service name
                    name = repo['name']
                    services.append({
                        'name': name,
                        'image': image,
                        'domain': f"{name}.{os.getenv('BASE_DOMAIN')}"
                    })
            except Exception as e:
                print(f"Error getting tags for repository {repo['name']}: {e}")
                continue
        
        print(f"Found {len(services)} services in registry")
        return services
    except Exception as e:
        print(f"Error listing registry images: {e}")
        if hasattr(e, 'stderr'):
            print(f"Error output: {e.stderr}")
        return []

def update_services_yaml(services):
    """Update services.yml with current registry state"""
    try:
        parent_dir = os.path.dirname(os.path.dirname(__file__))
        services_path = os.path.join(parent_dir, 'vars', 'services.yml')
        
        config = {
            'api_services': services
        }
        
        with open(services_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False)
    except Exception as e:
        print(f"Error updating services.yml: {e}")

@app.route('/')
def dashboard():
    docker_available = client is not None
    services = []
    orphaned_containers = []
    error_message = None

    if not docker_available:
        error_message = "Docker is not available. Please check server setup and configuration."
    else:
        try:
            # Get services from registry
            services = list_registry_images()
            containers = client.containers.list(all=True)
            
            # Track which containers we've matched
            matched_containers = set()
            
            # Enhance service info with container status
            for service in services:
                container = next((c for c in containers if c.name == service['name']), None)
                if container:
                    matched_containers.add(container.id)
                    service['status'] = container.status
                    service['running'] = container.status == 'running'
                    service['logs'] = container.logs(tail=5).decode('utf-8').split('\n')
                    service['deployed'] = True
                    
                    # Check if container image matches registry
                    service['image_mismatch'] = container.image.tags[0] if container.image.tags else 'unknown' != service['image']
                else:
                    service['status'] = 'not deployed'
                    service['running'] = False
                    service['logs'] = []
                    service['deployed'] = False
                    service['image_mismatch'] = False
                
            # Find containers without registry entries
            for container in containers:
                if container.id not in matched_containers:
                    orphaned_containers.append({
                        'name': container.name,
                        'image': container.image.tags[0] if container.image.tags else 'unknown',
                        'status': container.status,
                        'running': container.status == 'running',
                        'logs': container.logs(tail=5).decode('utf-8').split('\n')
                    })
            
            # Update services.yml for Ansible
            update_services_yaml(services)
                
        except Exception as e:
            error_message = f"Error connecting to Docker: {e}"
            print(error_message)

    return render_template('dashboard.html', 
                         services=services,
                         orphaned_containers=orphaned_containers,
                         docker_available=docker_available,
                         error_message=error_message,
                         base_domain=os.getenv('BASE_DOMAIN', 'alexpineda.ca'))

# Add migration endpoint for initial setup
@app.route('/migrate', methods=['POST'])
def migrate():
    try:
        migrate_from_yaml()
        flash('Successfully migrated services from YAML to database', 'success')
    except Exception as e:
        flash(f'Error during migration: {str(e)}', 'error')
    return redirect(url_for('dashboard'))

@app.route('/deploy', methods=['POST'])
def deploy():
    if not client:
        flash('Docker is not available. Please check server setup.', 'error')
        return redirect(url_for('dashboard'))

    image = request.form.get('image')
    domain = request.form.get('domain')
    
    if not image or not domain:
        flash('Image and domain are required', 'error')
        return redirect(url_for('dashboard'))
    # Show deployment progress page
    service_name = domain.split('.')[0]  # Extract subdomain as name
    return render_template('deploy_progress.html',
                         title='Deploying New Service',
                         message=f'Deploying {image} to {domain}...',
                         service_name=service_name)

@app.route('/service/<name>/restart', methods=['POST'])
def restart_service(name):
    if not client:
        flash('Docker is not available. Please check server setup.', 'error')
        return redirect(url_for('dashboard'))

    try:
        container = client.containers.get(name)
        container.restart()
        flash(f'Service {name} restarted successfully', 'success')
    except Exception as e:
        flash(f'Error restarting service: {str(e)}', 'error')
    return redirect(url_for('dashboard'))

@app.route('/service/<name>/shutdown', methods=['POST'])
def shutdown_service(name):
    if not client:
        flash('Docker is not available. Please check server setup.', 'error')
        return redirect(url_for('dashboard'))

    try:
        container = client.containers.get(name)
        container.stop()
        flash(f'Service {name} shut down successfully', 'success')
    except Exception as e:
        flash(f'Error shutting down service: {str(e)}', 'error')
    return redirect(url_for('dashboard'))

@app.route('/service/<name>/delete', methods=['POST'])
def delete_service(name):
    if not client:
        flash('Docker is not available. Please check server setup.', 'error')
        return redirect(url_for('dashboard'))

    try:
        container = client.containers.get(name)
        # Stop the container first if it's running
        if container.status == 'running':
            container.stop()
        # Remove the container
        container.remove()
        flash(f'Service {name} deleted successfully', 'success')
    except Exception as e:
        flash(f'Error deleting service: {str(e)}', 'error')
    return redirect(url_for('dashboard'))

def stream_ansible():
    try:
        # Get the parent directory (project root)
        parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dashboard_dir = os.path.dirname(os.path.abspath(__file__))
        ansible_dir = os.path.join(parent_dir, 'dashboard', 'ansible')
        os.chdir(ansible_dir)

        # Load environment variables
        load_dotenv()
        env = {
            'DROPLET_IP': os.getenv('DROPLET_IP'),
            'HOST_SSH_USER': os.getenv('HOST_SSH_USER', 'root'),
            'HOST_SSH_PASSWORD': os.getenv('HOST_SSH_PASSWORD'),
            'BASE_DOMAIN': os.getenv('BASE_DOMAIN', 'default.com'),
            'CADDY_EMAIL': os.getenv('CADDY_EMAIL', 'alexpineda@fastmail.com')
        }

        # Update domain.yml before redeploying
        try:
            from deploy import update_domain_yaml
            update_domain_yaml(env)
            yield "data: Updated domain configuration\n\n"
        except Exception as e:
            yield f"data: Warning: Could not update domain configuration: {str(e)}\n\n"

        # Get Docker config path
        docker_config = os.path.join(dashboard_dir, 'docker-config.json')
        if not os.path.exists(docker_config):
            yield f"data: Error: Docker config file not found at {docker_config}\n\n"
            return

        # Create inventory file
        with open('inventory.yml', 'w') as f:
            f.write(f"""all:
  hosts:
    api_server:
      ansible_host: {env['DROPLET_IP']}
      ansible_user: {env['HOST_SSH_USER']}
      ansible_password: {env['HOST_SSH_PASSWORD']}
      ansible_become_password: {env['HOST_SSH_PASSWORD']}
      docker_config_file: {docker_config}
""")

        try:
            # Run ansible-playbook with live output streaming
            process = subprocess.Popen(
                ['ansible-playbook', '-i', 'inventory.yml', 'playbook.yml'],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )

            # Stream output
            while True:
                output = process.stdout.readline()
                if output:
                    yield f"data: {output}\n\n"
                if process.poll() is not None:
                    break
            
            if process.returncode == 0:
                yield "data: Deployment completed successfully\n\n"
            else:
                yield "data: Deployment failed\n\n"

        finally:
            # Clean up inventory file
            if os.path.exists('inventory.yml'):
                os.remove('inventory.yml')
    except Exception as e:
        yield f"data: Error: {str(e)}\n\n"

def setup_deployment_env():
    """Setup common deployment environment and return configuration"""
    # Get the parent directory (project root)
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dashboard_dir = os.path.dirname(os.path.abspath(__file__))
    ansible_dir = os.path.join(parent_dir, 'dashboard', 'ansible')
    os.chdir(ansible_dir)

    # Load environment variables
    load_dotenv()
    env = {
        'DROPLET_IP': os.getenv('DROPLET_IP'),
        'HOST_SSH_USER': os.getenv('HOST_SSH_USER', 'root'),
        'HOST_SSH_PASSWORD': os.getenv('HOST_SSH_PASSWORD'),
        'BASE_DOMAIN': os.getenv('BASE_DOMAIN', 'default.com'),
        'CADDY_EMAIL': os.getenv('CADDY_EMAIL', 'alexpineda@fastmail.com')
    }

    # Get Docker config path
    docker_config = os.path.join(dashboard_dir, 'docker-config.json')
    
    return env, docker_config, parent_dir

def create_inventory_file(env, docker_config):
    """Create Ansible inventory file"""
    with open('inventory.yml', 'w') as f:
        f.write(f"""all:
  hosts:
    api_server:
      ansible_host: {env['DROPLET_IP']}
      ansible_user: {env['HOST_SSH_USER']}
      ansible_password: {env['HOST_SSH_PASSWORD']}
      ansible_become_password: {env['HOST_SSH_PASSWORD']}
      docker_config_file: {docker_config}
""")

def run_ansible_playbook(playbook, extra_vars=None):
    """Run Ansible playbook and yield output"""
    cmd = ['ansible-playbook', '-i', 'inventory.yml', playbook]
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

    while True:
        output = process.stdout.readline()
        if output:
            yield f"data: {output}\n\n"
        if process.poll() is not None:
            break
            
    return process.returncode == 0

def cleanup_deployment_files(extra_files=None):
    """Clean up temporary deployment files"""
    if os.path.exists('inventory.yml'):
        os.remove('inventory.yml')
    if extra_files:
        for file in extra_files:
            if os.path.exists(file):
                os.remove(file)

@app.route('/stream-deploy')
def stream_deploy():
    """Stream the output of a full server redeploy using playbook.yml"""
    def generate():
        try:
            # Get the parent directory (project root)
            parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            dashboard_dir = os.path.dirname(os.path.abspath(__file__))
            ansible_dir = os.path.join(parent_dir, 'dashboard', 'ansible')
            os.chdir(ansible_dir)

            # Load environment variables
            load_dotenv()
            env = {
                'DROPLET_IP': os.getenv('DROPLET_IP'),
                'HOST_SSH_USER': os.getenv('HOST_SSH_USER', 'root'),
                'HOST_SSH_PASSWORD': os.getenv('HOST_SSH_PASSWORD'),
                'BASE_DOMAIN': os.getenv('BASE_DOMAIN', 'default.com'),
                'CADDY_EMAIL': os.getenv('CADDY_EMAIL', 'alexpineda@fastmail.com')
            }

            # Update domain.yml before redeploying
            try:
                from deploy import update_domain_yaml
                update_domain_yaml(env)
                yield "data: Updated domain configuration\n\n"
            except Exception as e:
                yield f"data: Warning: Could not update domain configuration: {str(e)}\n\n"

            # Get Docker config path
            docker_config = os.path.join(dashboard_dir, 'docker-config.json')
            if not os.path.exists(docker_config):
                yield f"data: Error: Docker config file not found at {docker_config}\n\n"
                return

            # Create inventory file
            with open('inventory.yml', 'w') as f:
                f.write(f"""all:
  hosts:
    api_server:
      ansible_host: {env['DROPLET_IP']}
      ansible_user: {env['HOST_SSH_USER']}
      ansible_password: {env['HOST_SSH_PASSWORD']}
      ansible_become_password: {env['HOST_SSH_PASSWORD']}
      docker_config_file: {docker_config}
""")

            try:
                # Run ansible-playbook with live output streaming
                yield "data: Starting full server redeploy...\n\n"
                yield "data: This will update Docker, Caddy, and system dependencies\n\n"
                
                process = subprocess.Popen(
                    ['ansible-playbook', '-i', 'inventory.yml', 'playbook.yml'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )

                # Stream output
                while True:
                    output = process.stdout.readline()
                    if output:
                        yield f"data: {output}\n\n"
                    if process.poll() is not None:
                        break
                
                if process.returncode == 0:
                    yield "data: Server infrastructure deployment completed successfully\n\n"
                    
                    # Now deploy all services
                    yield "data: Starting API services deployment...\n\n"
                    process = subprocess.Popen(
                        ['ansible-playbook', '-i', 'inventory.yml', 'deploy.yml'],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        universal_newlines=True
                    )
                    
                    while True:
                        output = process.stdout.readline()
                        if output:
                            yield f"data: {output}\n\n"
                        if process.poll() is not None:
                            break
                    
                    if process.returncode == 0:
                        yield "data: API services deployment completed successfully\n\n"
                    else:
                        yield "data: API services deployment failed\n\n"
                else:
                    yield "data: Server infrastructure deployment failed\n\n"

            finally:
                # Clean up inventory file
                if os.path.exists('inventory.yml'):
                    os.remove('inventory.yml')
        except Exception as e:
            yield f"data: Error: {str(e)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream'
    )

@app.route('/stream-deploy-service')
def stream_deploy_service():
    service_name = request.args.get('name')
    if not service_name:
        return Response("data: Error: No service name provided\n\n", mimetype='text/event-stream')
        
    # Get service info from registry instead of database
    services = list_registry_images()
    service = next((s for s in services if s['name'] == service_name), None)
    
    if not service:
        return Response("data: Error: Service not found in registry\n\n", mimetype='text/event-stream')
    
    def generate():
        try:
            # Setup deployment environment
            env, docker_config, parent_dir = setup_deployment_env()
            ansible_dir = os.path.join(parent_dir, 'dashboard', 'ansible')
            os.chdir(ansible_dir)
            
            if not os.path.exists(docker_config):
                yield f"data: Error: Docker config file not found at {docker_config}\n\n"
                return

            # Update domain configuration
            try:
                from deploy import update_domain_yaml
                update_domain_yaml(env)
                yield "data: Updated domain configuration\n\n"
            except Exception as e:
                yield f"data: Warning: Could not update domain configuration: {str(e)}\n\n"

            # Create inventory file
            create_inventory_file(env, docker_config)

            try:
                yield f"data: Starting deployment of service {service_name}...\n\n"
                
                # Create a temporary vars file for this specific service
                service_vars = {
                    'api_services': [{
                        'name': service['name'],
                        'image': service['image'],
                        'domain': service['domain'],
                    }]
                }
                
                temp_vars_file = 'vars/temp_service.yml'
                with open(temp_vars_file, 'w') as f:
                    yaml.dump(service_vars, f)
                
                # Run deployment with build step
                success = False
                for output in run_ansible_playbook('deploy.yml', f'@{temp_vars_file}'):
                    yield output
                    if "Deployment completed successfully" in output:
                        success = True
                
                if success:
                    yield "data: Service deployment completed successfully\n\n"
                else:
                    yield "data: Service deployment failed\n\n"

            finally:
                cleanup_deployment_files([temp_vars_file])
                
        except Exception as e:
            yield f"data: Error: {str(e)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream'
    )

@app.route('/redeploy-all', methods=['POST'])
def redeploy_all():
    if not client:
        flash('Docker is not available. Please check server setup.', 'error')
        return redirect(url_for('dashboard'))
        
    return render_template('deploy_progress.html', 
                         title='Redeploying All Services',
                         message='Redeploying all services...')

@app.route('/stream-deploy-services')
def stream_deploy_services():
    """Stream the output of deploying all API services using deploy.yml"""
    def generate():
        try:
            # Get the parent directory (project root)
            parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            os.chdir(parent_dir)

            # Load environment variables
            load_dotenv()
            env = {
                'DROPLET_IP': os.getenv('DROPLET_IP'),
                'HOST_SSH_USER': os.getenv('HOST_SSH_USER', 'root'),
                'HOST_SSH_PASSWORD': os.getenv('HOST_SSH_PASSWORD'),
                'BASE_DOMAIN': os.getenv('BASE_DOMAIN', 'default.com'),
                'CADDY_EMAIL': os.getenv('CADDY_EMAIL', 'alexpineda@fastmail.com')
            }

            # Update domain.yml before redeploying
            try:
                from deploy import update_domain_yaml
                update_domain_yaml(env)
                yield "data: Updated domain configuration\n\n"
            except Exception as e:
                yield f"data: Warning: Could not update domain configuration: {str(e)}\n\n"

            # Get Docker config path
            docker_config = os.path.join(parent_dir, 'docker-config.json')
            if not os.path.exists(docker_config):
                yield f"data: Error: Docker config file not found at {docker_config}\n\n"
                return

            # Create inventory file
            with open('inventory.yml', 'w') as f:
                f.write(f"""all:
  hosts:
    api_server:
      ansible_host: {env['DROPLET_IP']}
      ansible_user: {env['HOST_SSH_USER']}
      ansible_password: {env['HOST_SSH_PASSWORD']}
      ansible_become_password: {env['HOST_SSH_PASSWORD']}
      docker_config_file: {docker_config}
""")

            try:
                # Run ansible-playbook with live output streaming
                yield "data: Starting API services deployment...\n\n"
                process = subprocess.Popen(
                    ['ansible-playbook', '-i', 'inventory.yml', 'deploy.yml'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )

                # Stream output
                while True:
                    output = process.stdout.readline()
                    if output:
                        yield f"data: {output}\n\n"
                    if process.poll() is not None:
                        break
                
                if process.returncode == 0:
                    # Update last_deployed timestamp for all services
                    services = ApiService.query.all()
                    for service in services:
                        service.last_deployed = datetime.utcnow()
                    db.session.commit()
                    yield "data: API services deployment completed successfully\n\n"
                else:
                    yield "data: API services deployment failed\n\n"

            finally:
                # Clean up inventory file
                if os.path.exists('inventory.yml'):
                    os.remove('inventory.yml')
        except Exception as e:
            yield f"data: Error: {str(e)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream'
    )

if __name__ == '__main__':
    print(f"Connecting to Docker at {DOCKER_HOST}")
    if not client:
        print("Warning: Docker is not available!")
    app.run(host='localhost', port=3000, debug=True) 