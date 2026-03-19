# Admin API Authentication Setup

This document explains how to set up and use the admin API key for protected endpoints like `/delete/userdata`.

## Local Development

### 1. Create `.env` file

Copy the example file and add your own values:

```bash
cp .env.example .env
```

Edit `.env` with your values:

```
ADMIN_API_KEY=dev-test-key-for-local-testing
HF_TOKEN=hf_your_token_here
```

**Important:** Never commit `.env` to git. It's already in `.gitignore`.

### 2. Install python-dotenv

```bash
pip install python-dotenv
```

Or if using conda:

```bash
conda install -c conda-forge python-dotenv
```

### 3. Run the server locally

```bash
fastapi run app/main.py
```

The `.env` file will be automatically loaded.

### 4. Test the admin endpoint

```bash
# Delete user data with valid key
curl -X DELETE http://localhost:8000/delete/userdata \
  -H "X-API-Key: dev-test-key-for-local-testing" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "guid=test-user-guid"

# Test with invalid key (should return 403)
curl -X DELETE http://localhost:8000/delete/userdata \
  -H "X-API-Key: wrong-key" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "guid=test-user-guid"
```

## Production/Server Deployment

This follows the same pattern as `HF_TOKEN` in [setup.sh](setup.sh). The `ADMIN_API_KEY` should be passed as an environment variable to the container.

### 1. Generate a secure admin key

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

This outputs a secure random key. Example: `9Z_mK7xL2pQ4rN8vW1sT6yU3aBcDeF5gHiJkLmNoP_q`

### 2. Set environment variables on your server

#### Option A: Pass via environment variable when running compose (recommended)

```bash
# Run with environment variable passed directly
ADMIN_API_KEY=9Z_mK7xL2pQ4rN8vW1sT6yU3aBcDeF5gHiJkLmNoP_q podman compose up -d
```

Or in a shell script:

```bash
#!/bin/bash
export ADMIN_API_KEY="9Z_mK7xL2pQ4rN8vW1sT6yU3aBcDeF5gHiJkLmNoP_q"
podman compose up -d
```

#### Option B: Using env-file with restricted permissions (most secure)

Similar to the approach in [setup.sh](setup.sh#L422), create a temporary environment file with restricted permissions:

```bash
# Create a temporary env file with restricted permissions
token_file=$(mktemp)
echo "ADMIN_API_KEY=9Z_mK7xL2pQ4rN8vW1sT6yU3aBcDeF5gHiJkLmNoP_q" > "$token_file"
chmod 600 "$token_file"

# Run podman compose with the env file
podman compose --env-file "$token_file" up -d

# Clean up the temporary file
rm "$token_file"
```

Or integrate into your systemd service startup script:

```bash
#!/bin/bash
set -euo pipefail

token_file=$(mktemp)
echo "ADMIN_API_KEY=${ADMIN_API_KEY}" > "$token_file"
chmod 600 "$token_file"

trap "rm -f $token_file" EXIT

podman compose --env-file "$token_file" up -d
```

#### Option C: Using systemd environment file (for systemd service)

Store the key in a systemd environment file:

```bash
# Create environment file (root-owned, restricted permissions)
sudo tee /etc/default/dta-server > /dev/null <<EOF
ADMIN_API_KEY=9Z_mK7xL2pQ4rN8vW1sT6yU3aBcDeF5gHiJkLmNoP_q
HF_TOKEN=hf_xxxxx
EOF

sudo chmod 600 /etc/default/dta-server
```

Then in your systemd service file (`dta-compose.service`):

```ini
[Service]
EnvironmentFile=/etc/default/dta-server
ExecStart=/usr/bin/podman compose -f /path/to/compose.yaml up
```

### 3. Verify deployment

Once your server is running:

```bash
# Test the endpoint (replace with your server URL and API key)
curl -X DELETE https://your-server.com/delete/userdata \
  -H "X-API-Key: 9Z_mK7xL2pQ4rN8vW1sT6yU3aBcDeF5gHiJkLmNoP_q" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "guid=test-user-guid"
```

## Security Best Practices

1. **Generate strong keys**: Never hardcode keys; use `secrets.token_urlsafe()` to generate random keys
2. **Rotate keys regularly**: Change the admin key periodically
3. **Limit access**: Only expose admin endpoints on private networks when possible
4. **Use HTTPS**: Always use HTTPS in production (not just HTTP)
5. **Log access**: The server logs all admin deletions for audit trails
6. **Environment isolation**: Use different keys for dev, staging, and production
7. **Secret management**: For enterprise deployments, consider using:
   - HashiCorp Vault
   - AWS Secrets Manager
   - Kubernetes Secrets
   - Docker Secrets (for swarm mode)

## Troubleshooting

### "Admin API key not configured" error

The `ADMIN_API_KEY` environment variable is not set. Make sure:

- The `.env` file exists in the project root (for local dev)
- The environment variable is exported before running (for production)
- The compose.yaml is passing the variable to the container

### "Invalid or missing API key" error (403)

The API key in the request header doesn't match the configured key. Double-check:

- The key in the `X-API-Key` header matches the configured `ADMIN_API_KEY`
- The header name is exactly `X-API-Key` (case-sensitive)

### KeyError or dotenv not found

Install python-dotenv:

```bash
pip install python-dotenv
```

Or update environment.yaml if this is your conda environment.

## Implementation Details

The `ADMIN_API_KEY` implementation follows the same pattern as `HF_TOKEN` in [setup.sh](setup.sh):

1. **Environment Variable Loading** — The key is read from the environment via `os.getenv("ADMIN_API_KEY", "")` in [app/main.py](app/main.py)
2. **Development Support** — Uses `python-dotenv` to load from `.env` files locally (see lines 6-7 in [app/main.py](app/main.py))
3. **Container Passing** — The `compose.yaml` passes the variable to the container with `${ADMIN_API_KEY:-}` (see [compose.yaml](compose.yaml#L15))
4. **Setup Script Compatible** — Can be passed to setup.sh scripts via environment, just like `HF_TOKEN` (see [setup.sh line 155](setup.sh#L155))

For maximum security in production, use **Option B** (env-file with restricted permissions), which mirrors the pattern used in [setup.sh's download_model function](setup.sh#L410-L428).
