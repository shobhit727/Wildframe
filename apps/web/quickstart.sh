#!/bin/bash
# Wildframe Frontend Quick Start

echo "🚀 Wildframe Frontend Quick Start"
echo "=================================="

# Navigate to frontend
cd /home/phoenix/Desktop/wildframe/apps/web

# Install dependencies
echo "📦 Installing dependencies..."
npm install

# Create .env.local
echo "🔧 Setting up environment..."
cp .env.local.example .env.local
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" >> .env.local

# Start dev server
echo "🎬 Starting dev server..."
npm run dev

echo "✅ Frontend running on http://localhost:3000"
echo "📌 Backend should be running on http://localhost:8000"
