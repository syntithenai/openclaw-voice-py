# Available Wake Word Models

## OpenWakeWord (Built-in, No Download Needed)

These models are included with the openwakeword Python package. Just use the model name in configuration.

| Model Name | Wake Phrase | Use Case | Sensitivity |
|-----------|------------|----------|------------|
| **hey_mycroft** | "Hey Mycroft" | Default voice assistant | Good |
| **alexa** | "Alexa" | Amazon voice command | Good |
| **jarvis** | "Hey Jarvis" | Generic voice assistant | Good |
| **ok_google** | "OK Google" | Google voice command | Good |
| **timer** | "Timer" | Generic timer command | Moderate |
| **weather** | "Weather" | Generic weather keyword | Moderate |
| **americano** | "Americano" | Random word (test) | Moderate |
| **downstairs** | "Downstairs" | Random word (test) | Moderate |
| **grapefruit** | "Grapefruit" | Random word (test) | Moderate |
| **grasshopper** | "Grasshopper" | Random word (test) | Moderate |

### Quick Test Commands
```bash
# Try each model
python3 switch_wake_word.py openwakeword hey_mycroft && ./run_voice_demo.sh
python3 switch_wake_word.py openwakeword alexa && ./run_voice_demo.sh
python3 switch_wake_word.py openwakeword jarvis && ./run_voice_demo.sh
python3 switch_wake_word.py openwakeword ok_google && ./run_voice_demo.sh
```

## Mycroft Precise (Requires Manual Download)

High-quality models for speech-to-text, optimized for Raspberry Pi. Download from GitHub.

### Available Precise Models
Source: https://github.com/MycroftAI/precise-data/tree/master/models

| Model | Wake Phrase | File |
|-------|------------|------|
| **hey-mycroft** | "Hey Mycroft" | hey-mycroft.pb |
| **alexa** | "Alexa" | alexa.pb |
| **jarvis** | "Hey Jarvis" | jarvis.pb |
| **ok-google** | "OK Google" | ok-google.pb |
| **siri** | "Hey Siri" | siri.pb |
| **computer** | "Computer" | computer.pb |

### Download Instructions

```bash
# Example: Download hey-mycroft model
mkdir -p docker/wakeword-models
cd docker/wakeword-models

# Download model and params files
wget https://github.com/MycroftAI/precise-data/raw/master/models/hey-mycroft.pb
wget https://github.com/MycroftAI/precise-data/raw/master/models/hey-mycroft.pb.params

# Verify files exist
ls -lh hey-mycroft.pb*
```

### Setup for Precise

```env
WAKE_WORD_ENABLED=true
PRECISE_ENABLED=true
PRECISE_WAKE_WORD=hey_mycroft
PRECISE_MODEL_PATH=docker/wakeword-models/hey-mycroft.pb
PRECISE_CONFIDENCE=0.15
OPENWAKEWORD_ENABLED=false
PICOVOICE_ENABLED=false
```

```bash
python3 validate_wake_word_config.py
./run_voice_demo.sh
```

## Picovoice (Commercial License Required)

Professional-grade proprietary wake word detection.

### Getting Started with Picovoice

1. **Get API Key**
   - Sign up at https://picovoice.ai/
   - Create AccessKey
   - Copy AccessKey to PICOVOICE_KEY

2. **Configure**
   ```env
   WAKE_WORD_ENABLED=true
   PICOVOICE_ENABLED=true
   PICOVOICE_WAKE_WORD=picovoice
   PICOVOICE_KEY=<your-key-here>
   PICOVOICE_CONFIDENCE=0.5
   OPENWAKEWORD_ENABLED=false
   PRECISE_ENABLED=false
   ```

3. **Test**
   ```bash
   python3 validate_wake_word_config.py
   ./run_voice_demo.sh
   ```

## Comparison Table

| Aspect | OpenWakeWord | Precise | Picovoice |
|--------|-------------|---------|-----------|
| **Cost** | Free | Free | Paid |
| **License** | Apache 2.0 | Open | Proprietary |
| **Models Available** | 10 built-in | 5-6 available | Custom via API |
| **Platforms** | All | All | All |
| **Setup Effort** | Minimal | Moderate | Moderate |
| **Model Download** | Built-in | Manual | Via API |
| **Performance** | Good | Excellent | Excellent |
| **Recommended For** | Linux/macOS/Windows | Raspberry Pi | Commercial apps |

## Sensitivity Tuning Guide

### Too Many False Positives?
**Increase confidence threshold:**
```bash
python3 switch_wake_word.py openwakeword alexa 0.7
python3 switch_wake_word.py openwakeword alexa 0.8
```

### Too Many Misses?
**Decrease confidence threshold:**
```bash
python3 switch_wake_word.py openwakeword alexa 0.3
python3 switch_wake_word.py openwakeword alexa 0.2
```

### Recommended Starting Values
- **OpenWakeWord**: 0.4-0.6 (default: 0.5)
- **Precise**: 0.1-0.3 (default: 0.15)
- **Picovoice**: 0.4-0.6 (vendor recommended)

## Batch Download Script

Create all Precise models at once:
```bash
#!/bin/bash
mkdir -p docker/wakeword-models
cd docker/wakeword-models

MODELS=("hey-mycroft" "alexa" "jarvis" "ok-google" "siri" "computer")
BASE="https://github.com/MycroftAI/precise-data/raw/master/models"

for model in "${MODELS[@]}"; do
    echo "Downloading $model..."
    wget -q "$BASE/$model.pb" 2>/dev/null && echo "✓ $model.pb"
    wget -q "$BASE/$model.pb.params" 2>/dev/null && echo "✓ $model.pb.params"
done

echo "Done! ls -lh to verify"
```

## Testing Workflow

### 1. Test Default Configuration
```bash
python3 validate_wake_word_config.py
./run_voice_demo.sh
# Say: "hey mycroft"
```

### 2. Try Different Models
```bash
# Try Alexa
python3 switch_wake_word.py openwakeword alexa
./run_voice_demo.sh && sleep 2 && tail orchestrator_output.log | grep -i wake

# Try Jarvis
python3 switch_wake_word.py openwakeword jarvis
./run_voice_demo.sh && sleep 2 && tail orchestrator_output.log | grep -i wake
```

### 3. Adjust Sensitivity
```bash
# More sensitive
python3 switch_wake_word.py openwakeword alexa 0.3
./run_voice_demo.sh

# Less sensitive  
python3 switch_wake_word.py openwakeword alexa 0.7
./run_voice_demo.sh
```

### 4. Deploy to Production
```bash
# Save best configuration in .env
git add .env
git commit -m "Configure wake word with alexa model, confidence 0.5"
git push
```

## References

- [OpenWakeWord GitHub](https://github.com/openclawcompute/openwakeword)
- [Mycroft Precise GitHub](https://github.com/MycroftAI/precise)
- [Precise Model Data](https://github.com/MycroftAI/precise-data)
- [Picovoice Documentation](https://picovoice.ai/docs/)
