#!/usr/bin/env bash
# scripts/docker_run.sh - Build and run the PRIMCS Docker container, then print the MCP server URL.
set -euo pipefail

IMAGE_NAME=primcs
CONTAINER_NAME=primcs_server
PORT=9000

# Build the Docker image

echo "[docker_run] Building Docker image..."
docker build -t $IMAGE_NAME .

# Stop and remove any existing container with the same name
if docker ps -a --format '{{.Names}}' | grep -Eq "^${CONTAINER_NAME}$"; then
    echo "[docker_run] Removing existing container..."
    docker rm -f $CONTAINER_NAME
fi

# Run the container

echo "[docker_run] Starting Docker container..."
docker run -d --name $CONTAINER_NAME -p $PORT:9000 --env-file .env $IMAGE_NAME

# Print the MCP server URL

echo "[docker_run] MCP server is running at: http://localhost:${PORT}/mcp"

# Define cleanup function
cleanup() {
    echo "[docker_run] Stopping Docker container..."
    docker stop $CONTAINER_NAME
    docker rm $CONTAINER_NAME
    echo "[docker_run] Container stopped and removed."
    exit 0
}

# Trap SIGINT and SIGTERM to cleanup
trap cleanup SIGINT SIGTERM

# Wait until told to exit (block forever, or until killed)
docker logs -f $CONTAINER_NAME &
wait $! 