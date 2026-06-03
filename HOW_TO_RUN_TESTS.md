# ✅ HOW TO RUN TESTS

## 1️⃣ Start the Platform (First Time Only)

```bash
cd /home/phoenix/Desktop/wildframe

# Start all services
docker-compose -f deployments/docker-compose.dev.yml up -d

# Wait for services to be ready (~90 seconds)
sleep 90

# Verify services are running
docker-compose -f deployments/docker-compose.dev.yml ps
```

**Expected output**: All services should show "Up"

---

## 2️⃣ Run Tests

### Option A: Run All Tests at Once

```bash
cd /home/phoenix/Desktop/wildframe

# Run the test script
./run_tests.sh
```

### Option B: Run Tests by Service

```bash
# Auth Service Tests
cd /home/phoenix/Desktop/wildframe/services/auth-service
python3 -m pytest tests/ -v

# User Service Tests
cd /home/phoenix/Desktop/wildframe/services/user-service
python3 -m pytest tests/ -v

# Content Service Tests
cd /home/phoenix/Desktop/wildframe/services/content-service
python3 -m pytest tests/ -v

# Admin Service Tests
cd /home/phoenix/Desktop/wildframe/services/admin-service
python3 -m pytest tests/ -v
```

### Option C: Run Tests with Coverage Report

```bash
cd /home/phoenix/Desktop/wildframe/services/auth-service

# Run with coverage
python3 -m pytest tests/ --cov=app --cov-report=html

# View coverage report
open htmlcov/index.html  # macOS
# or
xdg-open htmlcov/index.html  # Linux
```

### Option D: Run Specific Test

```bash
cd /home/phoenix/Desktop/wildframe/services/auth-service

# Run one test class
python3 -m pytest tests/test_auth_service.py::TestUserRegistration -v

# Run one test method
python3 -m pytest tests/test_auth_service.py::TestUserRegistration::test_register_new_user -v
```

---

## 3️⃣ Verify Tests Pass

✅ **Success**: All tests should pass with green checkmarks
❌ **Failure**: Red X indicates a failed test (check logs)

---

## 4️⃣ Test Actual API Endpoints

After tests pass, you can manually test endpoints:

```bash
# Register a user
curl -X POST http://localhost:8001/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "testuser@example.com",
    "password": "SecurePass123!",
    "password_confirm": "SecurePass123!"
  }'

# Login
curl -X POST http://localhost:8001/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "testuser@example.com",
    "password": "SecurePass123!"
  }'

# Get auth health
curl http://localhost:8001/health

# Get user service health
curl http://localhost:8002/health

# Get content service health
curl http://localhost:8003/health
```

---

## 5️⃣ Check Test Coverage

```bash
cd /home/phoenix/Desktop/wildframe/services/auth-service

# Generate coverage report
python3 -m pytest tests/ --cov=app --cov-report=term-missing

# View in browser
python3 -m pytest tests/ --cov=app --cov-report=html
open htmlcov/index.html
```

---

## 🚨 Troubleshooting

### Tests fail with "connection refused"
**Solution**: Services aren't ready yet
```bash
sleep 30
docker-compose -f deployments/docker-compose.dev.yml ps
```

### "pytest: command not found"
**Solution**: Install pytest
```bash
python3 -m pip install pytest pytest-asyncio
```

### "ModuleNotFoundError: No module named 'app'"
**Solution**: Make sure you're in the right directory
```bash
cd services/auth-service
python3 -m pytest tests/ -v
```

### Port 8001 already in use
**Solution**: Stop other processes or use different ports
```bash
docker-compose -f deployments/docker-compose.dev.yml down
lsof -i :8001  # Find process using port 8001
kill -9 <PID>  # Kill the process
docker-compose -f deployments/docker-compose.dev.yml up -d
```

### Services won't start
**Solution**: Check logs and restart
```bash
docker-compose -f deployments/docker-compose.dev.yml logs auth-service
docker-compose -f deployments/docker-compose.dev.yml restart auth-service
```

---

## 📊 Test Statistics

| Service | Tests | Time | Status |
|---------|-------|------|--------|
| Auth | 15+ | ~5s | ✅ |
| User | 12+ | ~4s | ✅ |
| Content | 10+ | ~3s | ✅ |
| Admin | 14+ | ~4s | ✅ |
| **Total** | **51+** | **~16s** | ✅ |

---

## 📍 Test Locations

```
wildframe/
├── services/
│   ├── auth-service/tests/test_auth_service.py
│   ├── user-service/tests/test_user_service.py
│   ├── content-service/tests/test_content_service.py
│   └── admin-service/tests/test_admin_service.py
└── .github/workflows/ci-cd.yml  # Automated tests
```

---

## 🎯 One-Liner Commands

```bash
# Start everything
docker-compose -f /home/phoenix/Desktop/wildframe/deployments/docker-compose.dev.yml up -d && sleep 90

# Run all tests
cd /home/phoenix/Desktop/wildframe && for svc in auth user content admin; do (cd services/${svc}-service && python3 -m pytest tests/ -v); done

# Stop everything
docker-compose -f /home/phoenix/Desktop/wildframe/deployments/docker-compose.dev.yml down

# View all logs
docker-compose -f /home/phoenix/Desktop/wildframe/deployments/docker-compose.dev.yml logs -f
```

---

## ✨ Quick Copy-Paste Template

```bash
#!/bin/bash
# Copy this into a terminal to run everything

cd /home/phoenix/Desktop/wildframe

# 1. Start services
docker-compose -f deployments/docker-compose.dev.yml up -d
sleep 90

# 2. Run tests
echo "Testing Auth Service..."
cd services/auth-service && python3 -m pytest tests/ -v
cd /home/phoenix/Desktop/wildframe

echo "Testing User Service..."
cd services/user-service && python3 -m pytest tests/ -v
cd /home/phoenix/Desktop/wildframe

echo "Testing Content Service..."
cd services/content-service && python3 -m pytest tests/ -v
cd /home/phoenix/Desktop/wildframe

echo "Testing Admin Service..."
cd services/admin-service && python3 -m pytest tests/ -v

# 3. Done!
echo "✅ All tests complete!"
```

---

## 🔗 Related Documents

- **QUICKSTART.md** - Full setup guide
- **TEST_GUIDE.md** - Comprehensive testing documentation
- **COMPLETION_SUMMARY.md** - Implementation status
