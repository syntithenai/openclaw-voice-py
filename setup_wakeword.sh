#!/bin/bash

# Setup script for wake word engines on Raspberry Pi

set -e

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}OpenClaw Voice - Wake Word Engine Setup${NC}"
echo ""
echo "Available engines:"
echo "  1) Mycroft Precise (RECOMMENDED for Raspberry Pi)"
echo "  2) Picovoice Porcupine (Requires API key)"
echo "  3) OpenWakeWord (Not compatible with ARMv7)"
echo ""
echo "Enter choice (1-3) or skip:"
read -p "Choice: " engine_choice

case "$engine_choice" in
    1)
        echo -e "${YELLOW}Setting up Mycroft Precise...${NC}"
        
        # Use venv if available
        if [ -f ".venv_orchestrator/bin/activate" ]; then
            source .venv_orchestrator/bin/activate
        elif [ -f ".venv311/bin/activate" ]; then
            source .venv311/bin/activate
        fi
        
        pip install --upgrade pip
        pip install precise-runner

        echo ""
        echo "Checking precise-engine binary..."
        if ! command -v precise-engine >/dev/null 2>&1; then
            echo -e "${YELLOW}⚠ precise-engine binary not found in PATH.${NC}"
            echo "  Option 3 flow: build a compatible ARMv7 artifact and deploy it:"
            echo "    ./build_precise_engine_armv7.sh"
            echo "    ./deploy_precise_engine_to_pi.sh pi ./artifacts/precise-engine-armv7/precise-engine.tar.gz"
        else
            if ! precise-engine --version >/dev/null 2>&1; then
                echo -e "${YELLOW}⚠ precise-engine exists but failed to run (likely Python ABI mismatch).${NC}"
                echo "  Option 3 flow: rebuild compatible artifact and deploy:"
                echo "    ./build_precise_engine_armv7.sh"
                echo "    ./deploy_precise_engine_to_pi.sh pi ./artifacts/precise-engine-armv7/precise-engine.tar.gz"
            else
                echo -e "${GREEN}✓ precise-engine executable looks healthy${NC}"
            fi
        fi

        echo ""
        echo "Downloading pre-trained model..."
        mkdir -p docker/wakeword-models
        
        echo "Download options:"
        echo "  1) hey-mycroft (\"Hey Mycroft\")"
        echo "  2) jarvis (\"Hey Jarvis\")"
        echo "  3) americano (\"Americano\")"
        echo ""
        read -p "Model choice (1-3): " model_choice
        
        case "$model_choice" in
            1)
                MODEL_NAME="hey-mycroft.pb"
                KEYWORD="hey-mycroft"
                ;;
            2)
                MODEL_NAME="jarvis.pb"
                KEYWORD="jarvis"
                ;;
            3)
                MODEL_NAME="americano.pb"
                KEYWORD="americano"
                ;;
            *)
                echo "Invalid choice, skipping model download"
                exit 0
                ;;
        esac
        
        MODEL_PATH="docker/wakeword-models/${MODEL_NAME}"
        
        # Try to download model, but don't fail if it can't
        if [ ! -f "$MODEL_PATH" ]; then
            echo "Attempting to download $MODEL_NAME..."
            
            # Try multiple sources
            MODEL_URLS=(
                "https://github.com/MycroftAI/precise-data/raw/models/${MODEL_NAME}"
                "https://raw.githubusercontent.com/MycroftAI/precise-data/models/${MODEL_NAME}"
                "https://github.com/MycroftAI/precise-data/raw/master/models/${MODEL_NAME}"
            )
            
            DOWNLOAD_SUCCESS=0
            for url in "${MODEL_URLS[@]}"; do
                if wget -q "$url" -O "$MODEL_PATH" 2>/dev/null; then
                    DOWNLOAD_SUCCESS=1
                    break
                fi
            done
            
            if [ $DOWNLOAD_SUCCESS -eq 1 ]; then
                echo -e "${GREEN}✓ Model downloaded to $MODEL_PATH${NC}"
            else
                echo -e "${YELLOW}⚠ Could not auto-download model. Model file must be provided manually.${NC}"
                echo ""
                echo "To get a model:"
                echo "  1. Visit: https://github.com/MycroftAI/precise-data"
                echo "  2. Download a .pb file (e.g., hey-mycroft.pb, jarvis.pb)"
                echo "  3. Place it in: $MODEL_PATH"
                echo ""
                DOWNLOAD_SUCCESS=0  # Mark as non-critical
            fi
        else
            echo -e "${GREEN}✓ Model already exists at $MODEL_PATH${NC}"
        fi
        
        echo ""
        echo -e "${GREEN}Precise Setup Complete!${NC}"
        echo ""
        echo "Configuration needed:"
        echo "  WAKE_WORD_ENABLED=true"
        echo "  WAKE_WORD_ENGINE=precise"
        echo "  WAKE_WORD_CONFIDENCE=0.5"
        echo ""
        echo "If precise-engine fails with Python ABI errors, use Option 3 scripts in repo root:"
        echo "  ./build_precise_engine_armv7.sh"
        echo "  ./deploy_precise_engine_to_pi.sh pi ./artifacts/precise-engine-armv7/precise-engine.tar.gz"
        echo ""
        if [ -f "$MODEL_PATH" ]; then
            echo -e "${GREEN}✓ Model file ready${NC}"
        else
            echo -e "${YELLOW}⚠ Model file must be downloaded manually${NC}"
        fi
        ;;
        
    2)
        echo -e "${YELLOW}Setting up Picovoice Porcupine...${NC}"
        
        # Use venv if available
        if [ -f ".venv_orchestrator/bin/activate" ]; then
            source .venv_orchestrator/bin/activate
        elif [ -f ".venv311/bin/activate" ]; then
            source .venv311/bin/activate
        fi
        
        pip install --upgrade pip
        pip install pvporcupine
        
        echo ""
        echo "Picovoice requires a free AccessKey from https://console.picovoice.co"
        echo ""
        read -p "Enter your Picovoice AccessKey: " access_key
        
        if [ -z "$access_key" ]; then
            echo -e "${RED}AccessKey cannot be empty${NC}"
            exit 1
        fi
        
        # Verify the key works
        if [ -f ".venv_orchestrator/bin/activate" ]; then
            source .venv_orchestrator/bin/activate
        elif [ -f ".venv311/bin/activate" ]; then
            source .venv311/bin/activate
        fi
        
        python3 -c "
import pvporcupine
try:
    porcupine = pvporcupine.create(access_key='$access_key', keywords=['alexa'])
    porcupine.delete()
    print('✓ AccessKey verified successfully')
except Exception as e:
    print('✗ AccessKey verification failed:', str(e))
    exit(1)
" || exit 1
        
        # Save to environment
        if [ -f ~/.bashrc ]; then
            if ! grep -q "PICOVOICE_ACCESS_KEY" ~/.bashrc; then
                echo "export PICOVOICE_ACCESS_KEY='$access_key'" >> ~/.bashrc
                echo -e "${GREEN}✓ AccessKey saved to ~/.bashrc${NC}"
            fi
        fi
        
        # Also add to current session
        export PICOVOICE_ACCESS_KEY="$access_key"
        
        echo ""
        echo -e "${GREEN}Picovoice setup complete!${NC}"
        echo ""
        echo "Add to your .env file:"
        echo "  WAKE_WORD_ENABLED=true"
        echo "  WAKE_WORD_ENGINE=picovoice"
        echo "  OPENWAKEWORD_MODEL_PATH=alexa  # or other keyword"
        echo "  WAKE_WORD_CONFIDENCE=0.5"
        echo ""
        echo "Don't forget to set PICOVOICE_ACCESS_KEY in your environment!"
        ;;
        
    3)
        echo -e "${YELLOW}OpenWakeWord is not compatible with Raspberry Pi 3 (ARMv7)${NC}"
        echo ""
        echo "Reason: Requires ONNX Runtime which doesn't support ARMv7"
        echo ""
        echo "Recommendation: Use Mycroft Precise instead (option 1)"
        ;;
        
    *)
        echo "Skipping wake word engine setup"
        ;;
esac
