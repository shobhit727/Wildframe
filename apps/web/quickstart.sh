#!/bin/bash
# Wildframe Frontend Quick Start

echo "🚀 Wildframe Frontend Quick Start"
echo "=================================="

# Navigate to frontend
cd /home/ph03n1x/Wildframe/apps/web

# Install dependencies
echo "📦 Installing dependencies..."
npm install

# Create .env.local
echo "🔧 Setting up environment..."
cp .env.local.example .env.local
cat <<'EOF' >> .env.local
NEXT_PUBLIC_API_URL=https://localhost:8000
EOF

# Start dev server
echo "🎬 Starting dev server..."
npm run dev

echo "✅ Frontend running on https://localhost:3000"
echo "📌 Backend should be running on https://localhost:8000"
