#!/bin/bash
set -e

echo "========================================="
echo "Starting Meduseld Deployment"
echo "Time: $(date)"
echo "========================================="

# Navigate to project directory
cd /app

# Pull latest changes
echo "Pulling latest code from GitHub..."
git pull origin main

# Rebuild and restart containers
echo "Rebuilding Docker containers..."
docker-compose down
docker-compose build --no-cache
docker-compose up -d

# Wait for health check
echo "Waiting for services to be healthy..."
sleep 10

# Check if services are running
if docker-compose ps | grep -q "Up"; then
    echo "========================================="
    echo "Deployment successful!"
    echo "Time: $(date)"
    echo "========================================="
else
    echo "========================================="
    echo "Deployment failed - services not running"
    echo "Time: $(date)"
    echo "========================================="
    exit 1
fi
