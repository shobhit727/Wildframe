#!/bin/bash
"""Start all services using docker-compose."""

set -e

ROOT="/home/phoenix/Desktop/wildframe"
COMPOSE_FILE="$ROOT/deployments/docker-compose.dev.yml"

echo "🚀 Starting Wildframe services..."

# Change to deployments directory
cd "$ROOT"

# Check if Docker is running
if ! docker ps > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker and try again."
    exit 1
fi

# Pull latest images
echo "📥 Pulling latest images..."
docker-compose -f "$COMPOSE_FILE" pull || true

# Build services
echo "🔨 Building services..."
docker-compose -f "$COMPOSE_FILE" build

# Start services
echo "🎯 Starting services..."
docker-compose -f "$COMPOSE_FILE" up -d

# Wait for services to be healthy
echo "⏳ Waiting for services to be healthy..."
sleep 10

# Check health
echo "🏥 Checking service health..."
docker-compose -f "$COMPOSE_FILE" ps

# Display service URLs
echo ""
echo -e "\033[1;32m✅ Services started successfully!\033[0m"
echo ""
echo "Service URLs:"
echo "  API Gateway: http://localhost:8000"
echo "  Auth Service: http://localhost:8001"
echo "  User Service: http://localhost:8002"
echo "  Content Service: http://localhost:8003"
echo "  Streaming Service: http://localhost:8004"
echo "  Search Service: http://localhost:8005"
echo "  Admin Service: http://localhost:8006"
echo "  Recommendation Service: http://localhost:8007"
echo "  Billing Service: http://localhost:8008"
echo "  Analytics Service: http://localhost:8009"
echo "  Notification Service: http://localhost:8010"
echo "  Media Pipeline Service: http://localhost:8011"
echo ""
echo "Monitoring:"
echo "  Prometheus: http://localhost:9090"
echo "  Grafana: http://localhost:3000"
echo "  Jaeger: http://localhost:16686"
echo "  Loki: http://localhost:3100"
echo ""
echo "To view logs: docker-compose -f $COMPOSE_FILE logs -f [service-name]"
echo "To stop services: docker-compose -f $COMPOSE_FILE down"
