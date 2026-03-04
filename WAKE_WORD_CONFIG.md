# Wake Word Engine Configuration Guide

This guide explains how to configure the OpenClaw Voice orchestrator with different wake word detection engines.

## Overview

The orchestrator supports three wake word detection engines:

| Engine | Platform | Model Format | License | Speed |
|--------|----------|-------------|---------|-------|
| **OpenWakeWord** | Linux/macOS/Windows | Built-in TFLite models | Apache 2.0 | Fast (~200ms) |
| **Precise** | Raspberry Pi/ARM | .pb binary models | Open (community) | Fast (~50ms) |
| **Picovoice** | All platforms | Proprietary models | Commercial | Fast |

## Configuration Structure

### 1. Global Settings
```env
WAKE_WORD_ENABLED=true                  # Enable/disable wake word detection
WAKE_WORD_TIMEOUT_MS=10000              # Timeout before returning to sleep
```

### 2. Choose ONE Engine

#### Option A: OpenWakeWord (Recommended for Linux/macOS/Windows)

```env
OPENWAKEWORD_ENABLED=true
OPENWAKEWORD_WAKE_WORD=hey_mycroft      # Descriptive label (info only)
OPENWAKEWORD_MODEL_PATH=hey_mycroft     # Model name or .tflite file path
OPENWAKEWORD_CONFIDENCE=0.5             # Detection threshold (0.0-1.0, lower=more sensitive)
```

**Available Models (built-in):**
- `hey_mycroft` (default, "Hello Mycroft")
- `alexa` ("Alexa")
- `americano` (sound keyword)
- `downstairs` (location keyword)
- `grapefruit` (random word)
- `grasshopper` (random word)
- `jarvis` ("Hey Jarvis")
- `ok_google` ("OK Google")
- `timer` (generic keyword)
- `weather` (generic keyword)

**Confidence Tuning:**
- `0.5` - Default (balanced)
- `0.3-0.4` - More sensitive (more false positives)
- `0.6-0.7` - Less sensitive (more misses)

#### Option B: Precise Engine (Recommended for Raspberry Pi)

```env
PRECISE_ENABLED=true
PRECISE_WAKE_WORD=hey_mycroft           # Descriptive label (info only)
PRECISE_MODEL_PATH=docker/wakeword-models/hey-mycroft.pb
PRECISE_CONFIDENCE=0.15                 # Detection threshold (0.0-1.0)
```

**Model Files:**
Each Precise model requires two files:
- `model_name.pb` - The neural network model
- `model_name.pb.params` - Model metadata

**Supported Models:**
- `hey-mycroft.pb` - "Hey Mycroft" wake word
  
To use other models, download from: https://github.com/MycroftAI/precise-data/tree/master/models

**Getting Models:**
```bash
# Download a Precise model
wget https://github.com/MycroftAI/precise-data/raw/master/models/hey-mycroft.pb
wget https://github.com/MycroftAI/precise-data/raw/master/models/hey-mycroft.pb.params

# Place in the wakeword-models directory
mv hey-mycroft.pb* docker/wakeword-models/
```

#### Option C: Picovoice Engine (Commercial)

```env
PICOVOICE_ENABLED=true
PICOVOICE_WAKE_WORD=picovoice           # Descriptive label (info only)
PICOVOICE_KEY=<your-access-key>         # API key from picovoice.ai
PICOVOICE_CONFIDENCE=0.5                # Detection threshold
```

### 3. Model Files Directory

```
docker/wakeword-models/
├── hey-mycroft.pb          # Precise model
├── hey-mycroft.pb.params   # Precise model metadata
└── (OpenWakeWord models are built-in from package)
```

## Examples

### Example 1: Ubuntu with OpenWakeWord (Default)
```env
WAKE_WORD_ENABLED=true
OPENWAKEWORD_ENABLED=true
OPENWAKEWORD_WAKE_WORD=hey_mycroft
OPENWAKEWORD_MODEL_PATH=hey_mycroft
OPENWAKEWORD_CONFIDENCE=0.5

PRECISE_ENABLED=false
PICOVOICE_ENABLED=false
```

### Example 2: Raspberry Pi with Precise
```env
WAKE_WORD_ENABLED=true
PRECISE_ENABLED=true
PRECISE_WAKE_WORD=hey_mycroft
PRECISE_MODEL_PATH=docker/wakeword-models/hey-mycroft.pb
PRECISE_CONFIDENCE=0.15

OPENWAKEWORD_ENABLED=false
PICOVOICE_ENABLED=false
```

### Example 3: Alternative Model (OpenWakeWord + Alexa)
```env
WAKE_WORD_ENABLED=true
OPENWAKEWORD_ENABLED=true
OPENWAKEWORD_WAKE_WORD=alexa
OPENWAKEWORD_MODEL_PATH=alexa
OPENWAKEWORD_CONFIDENCE=0.5

PRECISE_ENABLED=false
PICOVOICE_ENABLED=false
```

## Troubleshooting

### Wake word not detecting:
1. Check that `WAKE_WORD_ENABLED=true`
2. Verify exactly ONE engine is enabled
3. Check model file exists:
   ```bash
   ls -la docker/wakeword-models/
   ```
4. Verify model path in .env matches:
   - For OpenWakeWord: Use model name (e.g., `hey_mycroft`)
   - For Precise: Use full path to .pb file
5. Test audio input is working:
   ```bash
   python3 -m sounddevice  # Check mic input level
   ```
6. Check logs for specific errors:
   ```bash
   tail -f orchestrator_output.log | grep -i "wake\|precise\|openwakeword"
   ```

### "Audio detected but no spike" messages:
- Audio is being captured, but confidence isn't reaching threshold
- Try lowering confidence threshold
- Try different model/wake word
- Check microphone quality and positioning

### Precision issues on Pi:
- Ensure you're using Precise engine (not OpenWakeWord)
- Verify model file is not empty:
  ```bash
  ls -lh docker/wakeword-models/hey-mycroft.pb  # Should be > 10KB
  ```
- Check that TensorFlow warm-up completed:
  ```bash
  ssh pi "tail -20 orchestrator_output.log" | grep -i tensorflow
  ```

## Architecture Decisions

### Why separate variables per engine?

Instead of a single `WAKE_WORD_ENGINE` string variable, the new system uses:
- `PRECISE_ENABLED=true/false`
- `OPENWAKEWORD_ENABLED=true/false`
- `PICOVOICE_ENABLED=true/false`

**Benefits:**
1. Clear indication which engine is active
2. Engine-specific parameters clearly grouped
3. Validation ensures exactly one engine is enabled
4. Easier to test multiple engines (just toggle one at a time)
5. Self-documenting configuration

### Why engine-specific model paths?

- **OpenWakeWord**: Uses built-in model names (no file path needed)
- **Precise**: Uses file paths to .pb files
- **Picovoice**: Uses access key (no model file)

This reflects how each engine actually works and prevents mistakes like:
- Using Precise .pb files with OpenWakeWord
- Missing required .params files
- Wrong model name format

## Advanced Configuration

### Custom OpenWakeWord Model

You can use a custom .tflite model for OpenWakeWord:

```env
OPENWAKEWORD_MODEL_PATH=/path/to/custom_model.tflite
```

### Monitoring Wake Detection

View wake detection logs in real-time:
```bash
./run_voice_demo.sh 2>&1 | grep -E "Wake|Audio detected|confidence spike"
```

See all wake word events:
```bash
grep "Wake" orchestrator_output.log
```

### Performance Tuning

Adjust confidence thresholds based on false positives/misses:

**Too many false positives?** → Increase confidence threshold:
```env
OPENWAKEWORD_CONFIDENCE=0.7  # More strict
```

**Missing wake words?** → Decrease confidence threshold:
```env
OPENWAKEWORD_CONFIDENCE=0.3  # More sensitive
```

## References

- [OpenWakeWord GitHub](https://github.com/openclawcompute/openwakeword)
- [Mycroft Precise GitHub](https://github.com/MycroftAI/precise)
- [Precise Model Data](https://github.com/MycroftAI/precise-data)
- [Picovoice Documentation](https://picovoice.ai/)
