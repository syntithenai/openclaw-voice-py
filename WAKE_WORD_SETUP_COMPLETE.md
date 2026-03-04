# Wake Word Engine Configuration - Implementation Complete

## Summary

You now have a **distinct, engine-specific environment variable structure** for wake word detection. Instead of a single `WAKE_WORD_ENGINE` string, each engine has its own enabled flag and configuration parameters.

## What You Get

### 1. **Clear Engine Selection**
```env
# Choose exactly ONE:
PRECISE_ENABLED=false          # Mycroft Precise (Pi/ARM)
OPENWAKEWORD_ENABLED=true      # TFLite-based (Linux/macOS/Windows)
PICOVOICE_ENABLED=false        # Commercial (all platforms)
```

### 2. **Engine-Specific Configuration**
Each engine has its own:
- Wake word name/description
- Model path or model name
- Confidence threshold

No more mixing formats or accidentally using wrong model types!

### 3. **Multiple Public Models Available**

**OpenWakeWord (Built-in):**
- hey_mycroft, alexa, jarvis, ok_google, timer, weather, and more
- No downloads needed - included in package

**Precise (Mycroft):**
- Available from: https://github.com/MycroftAI/precise-data
- Each model includes .pb and .pb.params files

**Picovoice (Commercial):**
- Professional-grade
- Requires paid API key

## Files Created/Modified

### Configuration
- **`.env`** - Restructured with per-engine variables
- **`orchestrator/config.py`** - Added engine-specific fields and validation
- **`orchestrator/main.py`** - Updated detector initialization

### Documentation
- **`WAKE_WORD_CONFIG.md`** - Comprehensive configuration guide
- **`WAKE_WORD_IMPLEMENTATION.md`** - Technical details of changes
- **`WAKE_WORD_QUICK_START.md`** - Quick reference for testing

### Tools
- **`validate_wake_word_config.py`** - Check configuration validity
- **`switch_wake_word.py`** - Quick model/engine switching
- **`download_wakeword_models.py`** - Model downloader

## How to Use

### Start with Default Configuration
The system comes configured with **OpenWakeWord + hey_mycroft** ready to go:

```bash
./run_voice_demo.sh
# Say: "hey mycroft"
```

### Switch to Different Model Quickly
```bash
python3 switch_wake_word.py openwakeword alexa
python3 switch_wake_word.py openwakeword jarvis 0.4
python3 switch_wake_word.py openwakeword ok_google
```

### Enable Precise (for Raspberry Pi)
```bash
python3 switch_wake_word.py precise 0.15
```

### Check Configuration
```bash
python3 validate_wake_word_config.py
python3 switch_wake_word.py config
```

## Configuration Structure

```
WAKE_WORD_ENABLED=true                 # Master enable/disable
WAKE_WORD_TIMEOUT_MS=10000             # Timeout before sleep

# CHOOSE ONE:
PRECISE_ENABLED=true/false             # Mycroft Precise
  PRECISE_WAKE_WORD=<label>            # Description
  PRECISE_MODEL_PATH=<path>            # Path to .pb file
  PRECISE_CONFIDENCE=<0.0-1.0>         # Threshold

OPENWAKEWORD_ENABLED=true/false        # OpenWakeWord (TFLite)
  OPENWAKEWORD_WAKE_WORD=<label>       # Description
  OPENWAKEWORD_MODEL_PATH=<name>       # Model name
  OPENWAKEWORD_CONFIDENCE=<0.0-1.0>    # Threshold

PICOVOICE_ENABLED=true/false           # Picovoice
  PICOVOICE_WAKE_WORD=<label>          # Description
  PICOVOICE_KEY=<key>                  # API key
  PICOVOICE_CONFIDENCE=<0.0-1.0>       # Threshold
```

## Key Features

✓ **Validation** - Config validation ensures exactly one engine is enabled  
✓ **Self-Documenting** - Engine choice is immediately obvious from .env  
✓ **Easy Testing** - Switch models with one command  
✓ **Engine Separation** - No model format confusion  
✓ **Backward Compatible** - Old system cleanly replaced  
✓ **Multiple Models** - 10+ OpenWakeWord models available  

## Next Steps

1. **Test Current Setup**
   ```bash
   python3 validate_wake_word_config.py
   ./run_voice_demo.sh
   ```

2. **Try Different Models**
   ```bash
   python3 switch_wake_word.py openwakeword alexa
   ./run_voice_demo.sh
   ```

3. **Read Full Guides**
   - Quick start: [WAKE_WORD_QUICK_START.md](WAKE_WORD_QUICK_START.md)
   - Full config: [WAKE_WORD_CONFIG.md](WAKE_WORD_CONFIG.md)
   - Technical: [WAKE_WORD_IMPLEMENTATION.md](WAKE_WORD_IMPLEMENTATION.md)

4. **Deploy to Pi**
   - Sync updated code to Pi
   - Configure `PRECISE_ENABLED=true` in .env
   - Ensure Precise model files exist

## Architecture Benefits

### For Users
- Clear what wake word engine is active
- Easy to switch between models for testing
- Validation prevents configuration errors
- No accidental model format mixing

### For Developers
- Engine selection via boolean flags (not strings)
- Engine-specific config grouped logically
- Cleaner code (no string comparisons)
- Easier to add new engines in future

## Support

If you encounter issues:

1. **Validate configuration:**
   ```bash
   python3 validate_wake_word_config.py
   ```

2. **Check logs:**
   ```bash
   tail -f orchestrator_output.log | grep -i "wake\|precise\|openwakeword"
   ```

3. **Test audio:**
   ```bash
   python3 -m sounddevice
   ```

4. **See troubleshooting section in [WAKE_WORD_CONFIG.md](WAKE_WORD_CONFIG.md)**

---

**Current Status:** ✓ System configured and validated with OpenWakeWord engine
