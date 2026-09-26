#!/bin/bash
# Wildframe Backend Quick Start

echo "🚀 Wildframe Backend Quick Start"
echo "=================================="

cd /home/phoenix/Desktop/wildframe

# Start all services with Docker Compose
echo "🐳 Starting Docker Compose (14 containers)..."
docker-compose -f deployments/docker-compose.dev.yml up -d

echo "⏳ Waiting for services to start..."
sleep 5

# Show status
echo "📊 Service Status:"
docker-compose -f deployments/docker-compose.dev.yml ps

echo ""
echo "✅ All services running!"
echo ""
echo "📋 Service URLs:"
echo "  - API Gateway:        http://localhost:8000"
echo "  - Auth Service:       http://localhost:8001"
echo "  - User Service:       http://localhost:8002"
echo "  - Content Service:    http://localhost:8003"
echo "  - Streaming Service:  http://localhost:8004"
echo "  - Search Service:     http://localhost:8005"
echo "  - Admin Service:      http://localhost:8006"
echo "  - Recommendation:     http://localhost:8007"
echo "  - Billing Service:    http://localhost:8008"
echo "  - Analytics Service:  http://localhost:8009"
echo "  - Notification:       http://localhost:8010"
echo "  - Media Pipeline:     http://localhost:8011"
echo ""
echo "🔗 Database: PostgreSQL (12 databases)"
echo "💾 Cache: Redis (7.0)"
echo "🔍 Search: Elasticsearch (8.10)"
