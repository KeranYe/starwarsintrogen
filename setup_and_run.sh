#!/usr/bin/env bash
# setup_and_run.sh
# Run this with Git Bash on Windows 11 to set up and launch the Star Wars Intro Editor.
# Usage: bash script/setup_and_run.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$PROJECT_DIR/.venv"

cd "$PROJECT_DIR"

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python -m venv .venv
fi

# Activate virtual environment (Git Bash path)
source .venv/Scripts/activate

# Install dependencies
echo "Installing dependencies..."
pip install --upgrade pip
pip install pillow numpy imageio imageio-ffmpeg

# Run the app
echo "Launching Star Wars Intro Editor..."
python script/star_wars_intro_editor.py
