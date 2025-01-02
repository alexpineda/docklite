from flask import Flask, render_template, request, redirect, url_for, flash, Response, stream_with_context
import docker
import yaml
import os
import subprocess
from docker.errors import DockerException
from dotenv import load_dotenv
import sys
import time

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)  # For flash messages

# Get Docker connection details from environment variables
DOCKER_HOST = os.getenv('DOCKER_HOST', f"tcp://{os.getenv('DROPLET_IP', 'localhost')}:2376")

# Get absolute path to certs directory
CERTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'certs'))
DOCKER_CERT_PATH = os.getenv('DOCKER_CERT_PATH', CERTS_DIR)
DOCKER_TLS_VERIFY = os.getenv('DOCKER_TLS_VERIFY', '1')

# Configure Docker client
docker_kwargs = {
    'base_url': DOCKER_HOST,
    'tls': docker.tls.TLSConfig(
        client_cert=(
            os.path.join(DOCKER_CERT_PATH, 'cert.pem'),
            os.path.join(DOCKER_CERT_PATH, 'key.pem')
        ),
        ca_cert=os.path.join(DOCKER_CERT_PATH, 'ca.pem'),
        verify=True
    )
} if DOCKER_TLS_VERIFY == '1' else {'base_url': DOCKER_HOST}

def check_docker():
    try:
        client = docker.DockerClient(**docker_kwargs)
        client.ping()
        return client
    except DockerException as e:
        print(f"Docker not available: {e}")
        return None

client = check_docker()

def load_services():
    try:
        with open('../vars/services.yml', 'r') as f:
            config = yaml.safe_load(f)
            return config.get('api_services', [])
    except FileNotFoundError:
        return []

def save_service(image, domain):
    try:
        if not client:
            return False, "Docker is not available. Please check server setup."

        # Run the deploy script
        result = subprocess.run(
            [sys.executable, 'deploy.py', image, domain],
            capture_output=True,
            text=True,
            check=True
        )
        return True, "Service deployed successfully"
    except subprocess.CalledProcessError as e:
        return False, f"Deployment failed: {e.stderr}"

@app.route('/')
def dashboard():
    docker_available = client is not None
    services = []
    error_message = None

    if not docker_available:
        error_message = "Docker is not available. Please check server setup and configuration."
    else:
        try:
            services = load_services()
            containers = client.containers.list(all=True)
            
            # Enhance service info with container status
            for service in services:
                container = next((c for c in containers if c.name == service['name']), None)
                if container:
                    service['status'] = container.status
                    service['running'] = container.status == 'running'
                    try:
                        service['logs'] = container.logs(tail=5).decode('utf-8').split('\n')
                    except:
                        service['logs'] = []
                else:
                    service['status'] = 'not found'
                    service['running'] = False
                    service['logs'] = []
        except Exception as e:
            error_message = f"Error connecting to Docker: {e}"
            print(error_message)

    return render_template('dashboard.html', 
                         services=services, 
                         docker_available=docker_available,
                         error_message=error_message,
                         base_domain=os.getenv('BASE_DOMAIN', 'alexpineda.ca'))

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
    
    success, message = save_service(image, domain)
    if success:
        flash(message, 'success')
    else:
        flash(message, 'error')
    
    return redirect(url_for('dashboard'))

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