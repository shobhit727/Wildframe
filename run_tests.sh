#!/bin/bash
# Wildframe Platform - Quick Test Execution Script
# Run all tests with a single command

set -e

PROJECT_ROOT="/home/phoenix/Desktop/wildframe"
SERVICES=("auth-service" "user-service" "content-service" "admin-service")

echo "🚀 Wildframe Test Execution"
echo "================================"
echo ""

# Check if docker-compose is running
echo "✓ Checking if services are running..."
cd "$PROJECT_ROOT"

if ! docker-compose -f deployments/docker-compose.dev.yml ps | grep -q "Up"; then
    echo ""
    echo "⚠️  Services are not running!"
    echo ""
    echo "Start them with:"
    echo "  docker-compose -f deployments/docker-compose.dev.yml up -d"
    echo ""
    echo "Then wait 90 seconds for services to initialize:"
    echo "  sleep 90"
    echo ""
    exit 1
fi

echo "✓ Services are running"
echo ""

# Run tests for each service
echo "📊 Running Tests"
echo "================================"
echo ""

TOTAL_TESTS=0
FAILED_TESTS=0

for SERVICE in "${SERVICES[@]}"; do
    echo "🧪 Testing $SERVICE..."
    echo "─────────────────────────────────────────"
    
    SERVICE_PATH="$PROJECT_ROOT/services/$SERVICE"
    
    if [ ! -d "$SERVICE_PATH" ]; then
        echo "❌ $SERVICE not found at $SERVICE_PATH"
        FAILED_TESTS=$((FAILED_TESTS + 1))
        continue
    fi
    
    if [ ! -f "$SERVICE_PATH/tests/test_${SERVICE%-service}_service.py" ]; then
        echo "⊘  No tests found for $SERVICE"
        continue
    fi
    
    cd "$SERVICE_PATH"
    
    if python3 -m pytest tests/ -v --tb=short 2>&1; then
        echo "✅ $SERVICE tests passed"
    else
        echo "❌ $SERVICE tests failed"
        FAILED_TESTS=$((FAILED_TESTS + 1))
    fi
    
    echo ""
done

echo ""
echo "📈 Test Results Summary"
echo "================================"

if [ $FAILED_TESTS -eq 0 ]; then
    echo "✅ All tests passed!"
    exit 0
else
    echo "❌ $FAILED_TESTS service(s) had test failures"
    exit 1
fi
