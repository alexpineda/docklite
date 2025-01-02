import os
import subprocess
from typing import Dict, List

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

    def get_image(self, name: str) -> Dict[str, str]:
        """Get an image from the registry by name"""
        services = self.list_images()
        return next((service for service in services if service['name'] == name), None)
