# Ansible API Deployment Setup

## Architecture
- Local Flask dashboard for monitoring
- Remote Docker deployments on DigitalOcean
- Caddy for reverse proxy and SSL
- All services expose port 3000

## Project Structure
```
├── dashboard/           # Local monitoring dashboard (runs on Mac)
├── vars/               # Ansible variables
├── templates/          # Ansible templates
│   └── caddy/         # Caddy reverse proxy configs
└── deploy-api.sh      # Main deployment script
```

## Deployment
1. Services are deployed via:
   ```bash
   ./deploy-api.sh <docker-image> [--full-redeploy]
   ```
   Example:
   ```bash
   ./deploy-api.sh registry.digitalocean.com/api-alexpineda-containers/my-api:latest
   ```

2. Each service should:
   - Expose port 3000
   - Be available in the Docker registry
   - Support health checks

3. Service names and domains are automatically derived from the image name:
   - Image: `registry.digitalocean.com/api-alexpineda-containers/my-api:latest`
   - Service name: `my-api`
   - Domain: `my-api.{BASE_DOMAIN}`

## Dashboard
- Runs locally on Mac
- Connects to remote Docker daemon
- Requires TLS certificates in ~/.docker/machine/certs/
- Access at http://localhost:3000

## Configuration
- Services are discovered from Docker registry
- Each service needs:
  - To be pushed to the registry
  - Follow naming convention: `registry.digitalocean.com/api-alexpineda-containers/{service-name}:latest`

## Security and Configuration

### Sensitive Files
The following files contain sensitive information and are NOT included in the repository:

1. `.env` - Environment variables and credentials
   - Copy `.env.example` to `.env`
   - Fill in your actual values
   - Never commit this file

2. `docker-config.json` - Docker registry authentication
   - Copy `docker-config.json.example` to `docker-config.json`
   - Add your registry credentials
   - Never commit this file

3. `certs/` directory - TLS certificates
   - Contains: `ca.pem`, `cert.pem`, `key.pem`
   - Generate using `generate-certs.sh`
   - Never commit these files

### First-Time Setup
1. Copy example files:
   ```bash
   cp .env.example .env
   cp docker-config.json.example docker-config.json
   ```

2. Generate certificates:
   ```bash
   ./generate-certs.sh
   ```

3. Update the files with your actual values:
   - Edit `.env` with your server details
   - Add your registry credentials to `docker-config.json`

### Security Best Practices
1. Use strong passwords and keep them secure
2. Regularly rotate credentials and certificates
3. Restrict access to sensitive files:
   ```bash
   chmod 600 .env docker-config.json
   chmod 600 certs/*.pem
   ```
4. Keep your server's SSH keys secure
5. Regularly update dependencies and system packages 