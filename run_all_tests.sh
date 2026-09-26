#!/bin/bash
"""Run tests for all services."""

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

ROOT="/home/phoenix/Desktop/wildframe"
SERVICES=(
    "services/auth-service"
    "services/user-service"
    "services/content-service"
    "services/admin-service"
    "services/streaming-service"
    "services/search"
    "services/recommendation"
    "services/billing"
    "services/analytics"
    "services/notification"
    "services/media-pipeline"
)

echo -e "${YELLOW}🧪 Running all service tests...${NC}\n"

total_passed=0
total_failed=0

for service in "${SERVICES[@]}"; do
    service_name=$(basename "$service")
    echo -e "${YELLOW}Testing $service_name...${NC}"
    
    if [ -d "$ROOT/$service/app/tests" ]; then
        cd "$ROOT/$service"
        
        if python -m pytest app/tests -v --tb=short --color=yes 2>&1 | tee test_output.log; then
            echo -e "${GREEN}✅ $service_name tests passed${NC}\n"
            ((total_passed++))
        else
            echo -e "${RED}❌ $service_name tests failed${NC}\n"
            ((total_failed++))
        fi
    else
        echo -e "${YELLOW}⚠️  No tests found for $service_name${NC}\n"
    fi
done

echo -e "\n${YELLOW}📊 Test Summary${NC}"
echo -e "Passed: ${GREEN}$total_passed${NC}"
echo -e "Failed: ${RED}$total_failed${NC}"

if [ $total_failed -eq 0 ]; then
    echo -e "\n${GREEN}🎉 All tests passed!${NC}"
    exit 0
else
    echo -e "\n${RED}❌ Some tests failed${NC}"
    exit 1
fi
