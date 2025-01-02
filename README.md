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
   ./deploy-api.sh <docker-image> <domain>
   ```
2. Each service should:
   - Expose port 3000
   - Be available as a Docker image
   - Support health checks

## Dashboard
- Runs locally on Mac
- Connects to remote Docker daemon
- Requires TLS certificates in ~/.docker/machine/certs/
- Access at http://localhost:3000

## Configuration
- Services are tracked in vars/services.yml
- Each service needs:
  - name: derived from domain
  - image: Docker image
  - domain: service domain 

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