from flask import Flask, render_template, request, redirect, url_for, flash, Response, stream_with_context
import docker
import yaml
import os
import subprocess
from docker.errors import DockerException
from dotenv import load_dotenv
import sys
import time
from models import db, ApiService, init_db
from datetime import datetime

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)  # For flash messages

# Initialize database
init_db(app)

# Get Docker connection details from environment variables
DOCKER_HOST = os.getenv('DOCKER_HOST', f"tcp://{os.getenv('DROPLET_IP', 'localhost')}:2376")

# Define possible certificate locations, prioritizing dashboard directory
CERT_LOCATIONS = [
    os.path.join(os.path.dirname(__file__), 'certs'),  # Dashboard certs directory (preferred)
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

def migrate_from_yaml():
    """One-time migration from services.yml to database"""
    try:
        with open('../vars/services.yml', 'r') as f:
            config = yaml.safe_load(f)
            services = config.get('api_services', [])
            
            for service in services:
                existing = ApiService.query.filter_by(domain=service['domain']).first()
                if not existing:
                    new_service = ApiService(
                        name=service['name'],
                        image=service['image'],
                        domain=service['domain']
                    )
                    db.session.add(new_service)
            
            db.session.commit()
    except FileNotFoundError:
        pass

def update_services_yaml():
    """Update services.yml with current database state"""
    services = ApiService.query.all()
    config = {
        'api_services': [service.to_dict() for service in services]
    }
    
    with open('../vars/services.yml', 'w') as f:
        yaml.safe_dump(config, f)

def load_services():
    return ApiService.query.all()

def save_service(image, domain):
    try:
        if not client:
            return False, "Docker is not available. Please check server setup."

        name = domain.split('.')[0]  # Extract subdomain as name
        
        # Save to database
        service = ApiService(
            name=name,
            image=image,
            domain=domain
        )
        try:
            db.session.add(service)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return False, f"Database error: {str(e)}"
        
        try:
            # Update services.yml
            update_services_yaml()
        except Exception as e:
            # If YAML update fails, rollback database
            try:
                db.session.delete(service)
                db.session.commit()
            except:
                pass
            return False, f"YAML update failed: {str(e)}"

        return True, "Service saved successfully"
    except Exception as e:
        return False, str(e)

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
            services = load_services()
            containers = client.containers.list(all=True)
            
            # Track which containers we've matched
            matched_containers = set()
            
            # Enhance service info with container status
            for service in services:
                container = next((c for c in containers if c.name == service.name), None)
                if container:
                    matched_containers.add(container.id)
                    service.status = container.status
                    service.running = container.status == 'running'
                    service.logs = container.logs(tail=5).decode('utf-8').split('\n')
                    service.deployed = True
                    
                    # Check if container image matches database
                    service.image_mismatch = container.image.tags[0] if container.image.tags else 'unknown' != service.image
                else:
                    service.status = 'not found'
                    service.running = False
                    service.logs = []
                    service.deployed = False
                    service.image_mismatch = False
                
            # Find containers without database entries
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
    
    # Save service to database first
    success, message = save_service(image, domain)
    if not success:
        flash(message, 'error')
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

def stream_ansible():
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

@app.route('/stream-deploy')
def stream_deploy():
    return Response(
        stream_with_context(stream_ansible()),
        mimetype='text/event-stream'
    )

@app.route('/stream-deploy-service')
def stream_deploy_service():
    service_name = request.args.get('name')
    if not service_name:
        return Response("data: Error: No service name provided\n\n", mimetype='text/event-stream')
        
    service = ApiService.query.filter_by(name=service_name).first()
    if not service:
        return Response("data: Error: Service not found\n\n", mimetype='text/event-stream')
    
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
                process = subprocess.Popen(
                    ['ansible-playbook', '-i', 'inventory.yml', 'playbook.yml', '--limit', service_name],
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
                    # Update last_deployed timestamp
                    service.last_deployed = datetime.utcnow()
                    db.session.commit()
                    yield "data: Deployment completed successfully\n\n"
                else:
                    yield "data: Deployment failed\n\n"

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

@app.route('/redeploy-all', methods=['POST'])
def redeploy_all():
    if not client:
        flash('Docker is not available. Please check server setup.', 'error')
        return redirect(url_for('dashboard'))
        
    return render_template('deploy_progress.html', 
                         title='Redeploying All Services',
                         message='Redeploying all services...')

if __name__ == '__main__':
    print(f"Connecting to Docker at {DOCKER_HOST}")
    if not client:
        print("Warning: Docker is not available!")
    app.run(host='localhost', port=3000, debug=True) 