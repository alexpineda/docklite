import docker
import os
from docker.errors import DockerException
from typing import Optional, Dict
from datetime import datetime

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

    def get_container_logs(self, name: str) -> Dict:
        """Get container status"""
        if not self.client:
            raise Exception('Docker not available')
            
        container = self.client.containers.get(name)
        status = container.status
        logs = container.logs(tail=100, timestamps=True).decode('utf-8')
        
        inspect = container.attrs
        status_details = {
            'status': status,
            'state': inspect['State'],
            'platform': inspect['Platform'],
            'created': inspect['Created'],
            'started_at': inspect['State'].get('StartedAt'),
        }
        return [status, logs, status_details] 
    
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

    def container_restart(self, name: str) -> None:
        """Restart a container"""
        if not self.client:
            raise Exception('Docker not available')
            
        container = self.client.containers.get(name)
        container.restart()

    def container_shutdown(self, name: str) -> None:
        """Shutdown a container"""
        if not self.client:
            raise Exception('Docker not available')
            
        container = self.client.containers.get(name)
        container.stop()

    def container_delete(self, name: str) -> None:
        """Delete a container"""
        if not self.client:
            raise Exception('Docker not available')
            
        container = self.client.containers.get(name)
        if container.status == 'running':
            container.stop()
        container.remove()
