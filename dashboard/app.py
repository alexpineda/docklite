from managers.config_manager import ConfigManager
from flask import Flask, render_template, request, redirect, url_for, flash, Response, stream_with_context, jsonify
import os
from dotenv import load_dotenv
from typing import Dict, List, Optional,  Generator
from managers.docker_manager import DockerManager
from managers.ansible_manager import AnsibleManager
from managers.doctl_registry_manager import RegistryManager
from managers.caddy_manager import CaddyManager
import json
from pathlib import Path

# Load environment variables
load_dotenv()

# Initialize managers
docker_manager = DockerManager()
ansible_manager = AnsibleManager()
registry_manager = RegistryManager()
caddy_manager = CaddyManager()

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
            
    base_domain = ConfigManager().get_caddy_config().get('base_domain')
    return render_template('dashboard.html',
                         services=services,
                         orphaned_containers=orphaned_containers,
                         docker_available=docker_available,
                         error_message=error_message,
                         base_domain=base_domain)

def _stream_deployment(playbooks: str | list[str], extra_vars: Optional[str] = None) -> Generator[str, None, None]:
    """Generic deployment streaming function
    
    Args:
        playbooks: Either a single playbook string or list of playbook strings
        extra_vars: Extra vars to apply to all playbooks
    """
    try:
        cleanup_files = ansible_manager.setup_deployment()

        try:
            if isinstance(playbooks, str):
                yield f"data: Starting deployment with {playbooks}...\n\n"
            else:
                yield f"data: Starting deployment with playbooks: {', '.join(playbooks)}...\n\n"
                
            for output in ansible_manager.run_playbook(playbooks, extra_vars):
                yield output
                
            yield "Process completed"
                
        finally:
            for file in cleanup_files:
                if os.path.exists(file):
                    os.remove(file)
                    
    except Exception as e:
        yield f"data: Error: {str(e)}\n\n"


@app.route('/container/<name>/restart', methods=['POST'])
def restart_container(name):
    try:
        docker_manager.container_restart(name)
        flash(f'Service {name} restarted successfully', 'success')
    except Exception as e:
        flash(f'Error restarting service: {str(e)}', 'error')
    return redirect(url_for('dashboard'))

@app.route('/container/<name>/shutdown', methods=['POST'])
def shutdown_container(name):
    try:
        docker_manager.container_shutdown(name)
        flash(f'Container {name} shut down successfully', 'success')
    except Exception as e:
        flash(f'Error shutting down container: {str(e)}', 'error')
    return redirect(url_for('dashboard'))

@app.route('/container/<name>/delete', methods=['POST'])
def delete_container(name):
    try:
        docker_manager.container_delete(name)
        flash(f'Container {name} deleted successfully', 'success')
    except Exception as e:
        flash(f'Error deleting container: {str(e)}', 'error')
    return redirect(url_for('dashboard'))

@app.route('/deploy-machine-services')
def deploy_machine_services():
    """Stream the output of deploying machine services"""
    ansible_manager.prepare_services_vars(registry_manager.list_images(), write_to_file=True)

    return Response(
        stream_with_context(_stream_deployment(['playbook.yml', 'deploy.yml'])),
        mimetype='text/event-stream'
    )

@app.route('/deploy-all-containers')
def deploy_all_containers():
    """Stream the output of deploying all API services using deploy.yml"""
    ansible_manager.prepare_services_vars(registry_manager.list_images(), write_to_file=True)
        
    return Response(
        stream_with_context(_stream_deployment('deploy.yml')),
        mimetype='text/event-stream'
    )

@app.route('/deploy-container')
def deploy_container():
    """Stream single service deployment"""
    service_name = request.args.get('name')
    if not service_name:
        return Response("data: Error: No service name provided\n\n", mimetype='text/event-stream')
        
    service = registry_manager.get_image(service_name)
    
    if not service:
        return Response("data: Error: Service not found in registry\n\n", mimetype='text/event-stream')
        
    ansible_manager.prepare_services_vars([
        {
            'name': service['name'],
            'image': service['image'],
            'domain': service['domain']
        }
    ], write_to_file=True)
    
    return Response(
        stream_with_context(_stream_deployment('deploy.yml')),
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
        [ status, logs, status_details] = docker_manager.get_container_logs(name)
        
        return jsonify({
            'status': f"Status: {status}\nStarted: {status_details['started_at']}\nPlatform: {status_details['platform']}",
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
        
        result = caddy_manager.get_full_config(active_domains)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/cleanup-caddy-config', methods=['POST'])
def cleanup_caddy_config():
    """Move stale configuration files to backup directory"""
    try:
        # First, get the list of stale files
        services = registry_manager.list_images()
        active_domains = {service['domain'] for service in services}
        
        config_result = caddy_manager.get_full_config(active_domains)
        if 'error' in config_result:
            return jsonify({'error': config_result['error']}), 500
            
        if not config_result.get('stale_files'):
            return jsonify({'message': 'No stale configurations found'})
            
        stale_files = list(config_result['stale_files'].keys())
        result = caddy_manager.cleanup_stale_configs(stale_files)
        
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/test-caddy-config')
def test_caddy_config():
    """Test the Caddy configuration"""
    try:
        result = caddy_manager.test_config()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/global-config')
def get_global_config():
    """Get global configuration settings"""
    try:
        config_path = Path(__file__).parent / 'config.json'
        with open(config_path) as f:
            config = json.load(f)
            
        # Remove services and caddy sections as they're managed separately
        config_copy = config.copy()
        config_copy.pop('services', None)
        config_copy.pop('caddy', None)
            
        return jsonify(config_copy)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/global-config', methods=['POST'])
def update_global_config():
    """Update global configuration settings"""
    try:
        config_path = Path(__file__).parent / 'config.json'
        
        # Load existing config to preserve services and caddy sections
        with open(config_path) as f:
            current_config = json.load(f)
        
        # Get updated config from request
        new_config = request.json
        
        # Preserve services and caddy sections from current config
        if 'services' in current_config:
            new_config['services'] = current_config['services']
        if 'caddy' in current_config:
            new_config['caddy'] = current_config['caddy']
        
        # Save updated config
        with open(config_path, 'w') as f:
            json.dump(new_config, f, indent=2)
            
        return jsonify({'message': 'Configuration updated successfully'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/service/<name>/env', methods=['GET'])
def get_service_env(name):
    try:
        # Load config file
        config_path = Path(__file__).parent / 'config.json'
        with open(config_path) as f:
            config = json.load(f)
            
        # Get service env vars or empty dict if not found
        env_vars = config.get('services', {}).get(name, {}).get('env_vars', {})
        return jsonify(env_vars)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/service/<name>/env', methods=['POST'])
def update_service_env(name):
    try:
        # Load config file
        config_path = Path(__file__).parent / 'config.json'
        with open(config_path) as f:
            config = json.load(f)
            
        # Get the updated env vars from request
        env_vars = request.json
        
        # Ensure services dict exists
        if 'services' not in config:
            config['services'] = {}
            
        # Ensure service entry exists
        if name not in config['services']:
            config['services'][name] = {}
            
        # Update env vars
        config['services'][name]['env_vars'] = env_vars
        
        # Save config file
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
            
        # Return success
        return jsonify({'message': 'Environment variables updated successfully'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print(f"Connecting to Docker at {os.getenv('DOCKER_HOST')}")
    if not docker_manager.client:
        print("Warning: Docker is not available!")
    app.run(host='localhost', port=3000, debug=True) 