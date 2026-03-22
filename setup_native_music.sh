#!/bin/bash
# Setup script for native music backend integration (renamed to setup_native_music.sh)

set -e

echo "================================"
echo "Native Music Backend Setup"
echo "================================"

# Step 1: Check FFmpeg tooling
echo ""
echo "Step 1: Check FFmpeg tooling..."
if ! command -v ffmpeg &> /dev/null; then
    echo "  Installing ffmpeg..."
    sudo apt-get update
    sudo apt-get install -y ffmpeg
else
    echo "  ✓ ffmpeg found: $(which ffmpeg)"
fi

# Step 2: Create native music directories
echo ""
echo "Step 2: Ensure native music directories..."
mkdir -p ./music ./playlists ./.media
echo "  ✓ Directories ready"

# Step 3: Validate native backend
echo ""
echo "Step 3: Validate native backend..."
if .//home/stever/.pyenv/versions/3.11.9/bin/python3.11 validate_native_music_integration.py; then
    echo "  ✓ Native backend validation passed"
else
    echo "  ✗ Native backend validation failed"
    exit 1
fi

echo ""
echo "✓ All setup complete!"
echo ""
echo "Next steps:"
echo "  1. Run orchestrator: python3 orchestrator/main.py"
echo "  2. Run voice command: 'update library'"
echo "  3. Test music: 'play some jazz'"
echo ""
