#!/bin/bash
set -e

echo "========================================="
echo "Starting Meduseld Deployment"
echo "Time: $(date)"
echo "========================================="

# Navigate to project directory
cd /srv/apps/meduseld

# Pull latest changes
echo "Pulling latest code from GitHub..."
git reset --hard origin/main
git pull origin main

# Restart the service
echo "Restarting meduseld service..."
sudo systemctl restart meduseld

# Wait for health check
echo "Waiting for service to be healthy..."
sleep 5

# Check if service is running
if systemctl is-active --quiet meduseld; then
    echo "========================================="
    echo "Deployment successful!"
    echo "Time: $(date)"
    echo "========================================="
else
    echo "========================================="
    echo "Deployment failed - service not running"
    echo "Time: $(date)"
    echo "========================================="
    exit 1
fi
