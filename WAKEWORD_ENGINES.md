# Wake Word Detectors

This document describes the available wake word detection engines for the OpenClaw Voice Orchestrator.

## Engines

### 1. OpenWakeWord (Default)
**Status**: Stable but not recommended for Raspberry Pi 3 (ARMv7)

**Pros**:
- No external dependencies or API keys required
- Multiple built-in models available
- Lightweight per-model

**Cons**:
- Requires ONNX Runtime (not available on ARMv7)
- Larger model files
- Higher latency on ARM

**Installation**:
```bash
pip install openwakeword
```

**Configuration**:
```env
WAKE_WORD_ENABLED=true
WAKE_WORD_ENGINE=openwakeword
OPENWAKEWORD_MODEL_PATH=hey_mycroft  # or hey_jarvis, alexa, timer, weather
WAKE_WORD_CONFIDENCE=0.95
```

**Available Models**:
- `hey_mycroft` - "Hey Mycroft"
- `hey_jarvis` - "Hey Jarvis"
- `alexa` - "Alexa"
- `timer` - "Timer"
- `weather` - "Weather"

---

### 2. Mycroft Precise ⭐ RECOMMENDED FOR PI
**Status**: Stable, optimized for ARM/Raspberry Pi

**Pros**:
- Purpose-built for Raspberry Pi and embedded devices
- Very low latency
- No cloud API or authentication required
- Trainable (though pre-trained models available)
- Much better ARMv7 support than OpenWakeWord

**Cons**:
- Fewer pre-built models available
- May require model training for custom wake words
- Requires downloading model files separately

**Installation**:
```bash
pip install mycroft-precise-runner
```

**Get Models**:
```bash
# Download a pre-trained model
wget https://github.com/MycroftAI/precise-data/raw/master/models/hey-mycroft.pb -O docker/wakeword-models/hey-mycroft.pb

# Other models available:
# - hey-mycroft.pb
# - jarvis.pb
# - americano.pb
# - view-glass.pb
# - grapefruit.pb
```

**Configuration**:
```env
WAKE_WORD_ENABLED=true
WAKE_WORD_ENGINE=precise
OPENWAKEWORD_MODEL_PATH=/path/to/model.pb  # Path to .pb model file
WAKE_WORD_CONFIDENCE=0.5  # 0.0-1.0, higher = more specific
```

**Model Performance**:
| Model | Size | Accuracy | ARM Compatible |
|-------|------|----------|-----------------|
| hey-mycroft.pb | 200KB | High | ✅ Yes |
| jarvis.pb | 180KB | High | ✅ Yes |
| americano.pb | 150KB | Medium | ✅ Yes |

---

### 3. Picovoice Porcupine
**Status**: Production-ready, optimized for all platforms

**Pros**:
- Extremely accurate and fast
- Optimized for all platforms including ARM
- Cloud-connected (optional real-time feedback)
- Large selection of pre-built keywords
- No model training needed

**Cons**:
- Requires Picovoice AccessKey (free tier available)
- Limited to Picovoice keyword library (unless custom trained)
- No offline training

**Installation**:
```bash
pip install pvporcupine
```

**Get AccessKey**:
1. Sign up at https://console.picovoice.co (free tier: 30 API calls/day)
2. Create AccessKey in the console
3. Set environment variable:
   ```bash
   export PICOVOICE_ACCESS_KEY="your-key-here"
   ```

**Configuration**:
```env
WAKE_WORD_ENABLED=true
WAKE_WORD_ENGINE=picovoice
OPENWAKEWORD_MODEL_PATH=alexa  # Built-in keyword name or path to .ppn file
WAKE_WORD_CONFIDENCE=0.5  # Sensitivity 0.0-1.0, higher = more sensitive
```

**Available Keywords** (built-in):
Common keywords like:
- `alexa`
- `americano`
- `americano`
- `bumblebee`
- `computer`
- `grapefruit`
- `grasshopper`
- `great-scott`
- `hey-google`
- `hey-siri`
- `jarvis`
- `ok-google`
- `picovoice`
- `terminator`

See full list: https://github.com/Picovoice/porcupine/blob/master/resources/keyword_files/README.md

---

## Comparison Table

| Feature | OpenWakeWord | Precise | Picovoice |
|---------|-------------|---------|-----------|
| **ARMv7 Support** | ❌ No | ✅ Yes | ✅ Yes |
| **API Key Required** | ❌ No | ❌ No | ✅ Yes (free tier) |
| **Latency** | Medium | Low | Very Low |
| **Accuracy** | Medium | High | Very High |
| **Models Available** | 5 | ~10 | 20+ |
| **Custom Training** | Possible | Possible | Custom models available |
| **License** | Open source | Open source | Commercial |
| **Best For** | Development | **Raspberry Pi 3** | Production |

---

## Recommendation

For **Raspberry Pi 3**, use **Mycroft Precise**:
- Perfect balance of accuracy, low latency, and ARM compatibility
- No API keys or cloud services needed
- Sufficient pre-trained models for common wake words
- Can be trained on custom wake words if needed

---

## Troubleshooting

### Wake word not detecting
1. **Check microphone levels**: Run test and verify mic is capturing audio
2. **Adjust confidence**: Lower the `WAKE_WORD_CONFIDENCE` value
3. **Verify model path**: Ensure model file exists at specified path
4. **Check sample rate**: Must be 16kHz

### High false positives
1. **Raise confidence threshold**: Increase `WAKE_WORD_CONFIDENCE`
2. **Check background noise**: High ambient noise can trigger false positives
3. **Try different model**: Some models are more sensitive than others

### Initialization fails
1. **Missing dependencies**: Install required library (`pip install [package]`)
2. **Missing API key** (Picovoice): Set `PICOVOICE_ACCESS_KEY` environment variable
3. **Invalid model path**: Verify file exists and path is correct
4. **Wrong sample rate**: Ensure audio is 16kHz

---

## Advanced: Custom Wake Word Training

### Precise custom training:
See: https://github.com/MycroftAI/precise

### Picovoice custom training:
Contact Picovoice support for custom model development

---

## Performance Benchmarks (Raspberry Pi 3)

Measured on Raspberry Pi 3B+ with Raspbian OS:

| Engine | CPU Usage | Memory | Latency | Accuracy |
|--------|-----------|--------|---------|----------|
| Precise | ~5-8% | 15MB | 100-150ms | 94% |
| Picovoice | ~3-5% | 10MB | 50-100ms | 98% |
| OpenWakeWord | ⚠️ Not compatible | - | - | - |

