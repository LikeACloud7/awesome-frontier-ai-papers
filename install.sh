#!/bin/bash

# Daily AI Papers Installation Script

set -e

echo "📚 Daily AI Papers Installer"
echo "============================"
echo ""

# Get the directory where this script is located
PROJECT_PATH="$(cd "$(dirname "$0")" && pwd)"

echo "📁 Project path: $PROJECT_PATH"
echo ""

# 1. Create virtual environment
echo "🐍 Setting up Python virtual environment..."
if [ ! -d "$PROJECT_PATH/venv" ]; then
    python3 -m venv "$PROJECT_PATH/venv"
    echo "   ✓ Virtual environment created"
else
    echo "   ✓ Virtual environment already exists"
fi

# 2. Install dependencies
echo "📦 Installing Python dependencies..."
"$PROJECT_PATH/venv/bin/pip" install -q -r "$PROJECT_PATH/requirements.txt"
echo "   ✓ Dependencies installed"

# 3. Create output directory
mkdir -p "$PROJECT_PATH/output"
echo "   ✓ Output directory created"

# 4. Update command files with correct path
echo "🔧 Configuring Claude Code commands..."

mkdir -p ~/.claude/commands

# Update and copy papers.md
sed "s|{PROJECT_PATH}|$PROJECT_PATH|g" "$PROJECT_PATH/.claude/commands/papers.md" > ~/.claude/commands/papers.md
echo "   ✓ papers.md installed"

# Update and copy papers-config.md
sed "s|{PROJECT_PATH}|$PROJECT_PATH|g" "$PROJECT_PATH/.claude/commands/papers-config.md" > ~/.claude/commands/papers-config.md
echo "   ✓ papers-config.md installed"

echo ""
echo "✅ Installation complete!"
echo ""
echo "📖 Usage:"
echo "   1. Start Claude Code: claude"
echo "   2. Collect papers:    /papers"
echo "   3. Configure:         /papers-config"
echo ""
echo "🔄 Restart Claude Code to load the new commands."
