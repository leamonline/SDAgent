#!/bin/bash

# Test script for running with the real OpenAI Agents SDK
# Requires Python 3.10+ and OPENAI_API_KEY

set -e

echo "🔍 Checking Python version..."
source .venv-py312/bin/activate
PYTHON_VERSION=$(python --version)
echo "✅ Using: $PYTHON_VERSION"

echo ""
echo "🔍 Checking OpenAI API key..."
if [ -z "$OPENAI_API_KEY" ]; then
    echo "❌ ERROR: OPENAI_API_KEY environment variable is not set"
    echo ""
    echo "To run with the real SDK, set your OpenAI API key:"
    echo "  export OPENAI_API_KEY='your-api-key-here'"
    echo ""
    echo "Or run in stub mode (no API key needed):"
    echo "  python3 smarter_dog_refactored.py"
    exit 1
fi
echo "✅ OPENAI_API_KEY is set"

echo ""
echo "🔍 Verifying SDK installation..."
python -c "from agents import Agent; print('✅ OpenAI Agents SDK is installed')"

echo ""
echo "🚀 Running smarter_dog_refactored.py with REAL SDK..."
echo ""
python smarter_dog_refactored.py

echo ""
echo "✅ Test completed successfully!"
