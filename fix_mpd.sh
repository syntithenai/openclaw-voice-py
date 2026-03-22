#!/bin/bash
# Native music backend setup helper

set -e

echo "Setting up native music backend directories..."

# Create required local directories.
mkdir -p ./music
mkdir -p ./playlists
mkdir -p ./.media

echo "✓ Directories created"
ls -la ./music ./playlists ./.media

echo
echo "Directories ready. To initialize the media index run:"
echo "  ./.venv_orchestrator/bin/python validate_native_music_integration.py"
echo
echo "Then start orchestrator and run:"
echo "  /music scan"
