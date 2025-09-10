#!/bin/bash

echo "🚂 Starting Railway Control System Frontend..."
echo "📁 Changing to frontend directory..."
cd frontend

echo "📦 Installing dependencies..."
npm install

echo "🚀 Starting development server on http://localhost:5173"
echo "🛑 Press Ctrl+C to stop the server"
echo "----------------------------------------"

npm run dev
