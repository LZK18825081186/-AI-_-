#!/bin/bash

echo "Starting xianyu-auto-reply system..."

# Create necessary directories
mkdir -p /app/data /app/logs /app/backups /app/static/uploads/images

# Clean up old logs (keep 7 days)
find /app/logs -name "*.log" -mtime +7 -delete 2>/dev/null
echo "Log cleanup done (kept last 7 days)"

# Set permissions
chmod 777 /app/data /app/logs /app/backups /app/static/uploads /app/static/uploads/images

# Start the application
exec python Start.py
