from managers.ansible_manager import AnsibleManager
from managers.doctl_registry_manager import RegistryManager
import argparse

def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Deploy a container image')
    parser.add_argument('image_name', help='Name of the image to deploy')
    args = parser.parse_args()

    # Get the specified service
    registry_manager = RegistryManager()
    service = next(s for s in registry_manager.list_images() if s['name'] == args.image_name)
    
    # Prepare deployment
    ansible_manager = AnsibleManager()
    cleanup_files = ansible_manager.setup_deployment()
    
    try:
        # Prepare and write vars file
        ansible_manager.prepare_services_vars([service], write_to_file=True)
        print('Generated vars file. Running playbook...')
        
        # Run the deployment
        for output in ansible_manager.run_playbook('deploy.yml'):
            print(output)
    finally:
        # Cleanup temporary files
        import os
        for file in cleanup_files:
            if os.path.exists(file):
                os.remove(file)

if __name__ == '__main__':
    main() 