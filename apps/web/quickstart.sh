#!/bin/bash
# Wildframe Frontend Quick Start
set -e

echo "🚀 Wildframe Frontend Quick Start"
echo "=================================="

# Navigate to frontend
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

# Install dependencies
echo "📦 Installing dependencies..."
npm install

# Create .env.local
echo "🔧 Setting up environment..."
if [ ! -e .env.local ]; then
  cp .env.local.example .env.local
fi

# Start dev server
echo "🎬 Starting dev server..."
npm run dev

echo "✅ Frontend running on https://localhost:3000"
echo "📌 Backend should be running on https://localhost:8000"
