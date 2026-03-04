#!/bin/bash

# Helper script to stop the OpenClaw Voice Orchestrator

echo "Stopping orchestrator..."
pkill -f "python.*orchestrator.main" 2>/dev/null

# Wait a moment and verify
sleep 1

if pgrep -f "python.*orchestrator.main" > /dev/null 2>&1; then
    echo "❌ Orchestrator still running, forcing kill..."
    pkill -9 -f "python.*orchestrator.main" 2>/dev/null
    sleep 1
fi

if pgrep -f "python.*orchestrator.main" > /dev/null 2>&1; then
    echo "❌ Failed to stop orchestrator"
    exit 1
else
    echo "✓ Orchestrator stopped"
    exit 0
fi
