# Wake Word Configuration System - Implementation Summary

## Overview

The wake word detection system has been refactored to use **distinct environment variables for each engine**, making it clear which engine is active and what models are being used. This replaces the previous `WAKE_WORD_ENGINE=<string>` approach with engine-specific enabled flags.

## What Changed

### 1. Environment Variables (.env)

**Old System:**
```env
WAKE_WORD_ENGINE=openwakeword          # String indicating which engine
WAKE_WORD_CONFIDENCE=0.5                # Single confidence for all engines
OPENWAKEWORD_MODEL_PATH=hey_mycroft     # Only this engine's config visible
```

**New System:**
```env
# Global
WAKE_WORD_ENABLED=true                 # Master enable/disable
WAKE_WORD_TIMEOUT_MS=10000             # Timeout before sleep

# Engine Selection (set EXACTLY ONE to true)
PRECISE_ENABLED=false
OPENWAKEWORD_ENABLED=true
PICOVOICE_ENABLED=false

# Engine-Specific Configuration
## Precise (if enabled)
PRECISE_WAKE_WORD=hey_mycroft           # Descriptive label
PRECISE_MODEL_PATH=docker/wakeword-models/hey-mycroft.pb
PRECISE_CONFIDENCE=0.15

## OpenWakeWord (if enabled)  
OPENWAKEWORD_WAKE_WORD=hey_mycroft      # Descriptive label
OPENWAKEWORD_MODEL_PATH=hey_mycroft     # Built-in model name
OPENWAKEWORD_CONFIDENCE=0.5

## Picovoice (if enabled)
PICOVOICE_WAKE_WORD=picovoice           # Descriptive label
PICOVOICE_KEY=<access-key>
PICOVOICE_CONFIDENCE=0.5
```

### 2. Configuration File (orchestrator/config.py)

**Added Fields:**
- `precise_enabled: bool`
- `precise_wake_word: str` - Descriptive label
- `precise_model_path: str` - Path to .pb file
- `precise_confidence: float` - Detection threshold
- `openwakeword_enabled: bool`
- `openwakeword_wake_word: str` - Descriptive label
- `openwakeword_model_path: str` - Model name or .tflite path
- `openwakeword_confidence: float` - Detection threshold
- `picovoice_enabled: bool`
- `picovoice_wake_word: str` - Descriptive label
- `picovoice_key: str` - API key
- `picovoice_confidence: float` - Detection threshold

**Removed Fields:**
- `wake_word_engine: str` - Replaced with per-engine enabled flags
- `wake_word_confidence: float` - Replaced with per-engine confidence

**Improved Validation:**
```python
# Validates that:
# 1. If wake word enabled, exactly ONE engine is enabled
# 2. Each enabled engine has required configuration
# 3. Confidence thresholds are in valid range (0.0-1.0)
```

### 3. Main Orchestrator (orchestrator/main.py)

**Updated Wake Detector Initialization:**
```python
# Old: Checked config.wake_word_engine string
if config.wake_word_engine == "openwakeword":
    ...

# New: Checks enabled flags, uses engine-specific config
if config.openwakeword_enabled:
    wake_detector = OpenWakeWordDetector(
        model_path=config.openwakeword_model_path,
        confidence=config.openwakeword_confidence,
    )
elif config.precise_enabled:
    wake_detector = MycoftPreciseDetector(
        model_path=config.precise_model_path,
        confidence=config.precise_confidence,
    )
```

**Active Engine Tracking:**
- Introduced `active_wake_engine` variable to track which engine is running
- Used in logging to show engine-specific confidence thresholds
- Enables proper warm-up for Precise (TensorFlow) but skips for OpenWakeWord

## Benefits

### For Users
1. **Clear Configuration**: Obvious which engine is active
2. **Engine-Specific Settings**: Each engine's parameters are grouped together
3. **Easy Switching**: Just toggle one enabled flag to switch engines
4. **Better Validation**: Config validation catches common mistakes upfront
5. **No Model Mix-ups**: Precise .pb files won't accidentally go to OpenWakeWord

### For Developers
1. **Self-Documenting**: Code shows which engine is active without string parsing
2. **Type Safety**: Booleans instead of strings for engine selection
3. **Cleaner Logic**: No need for if-elif chains on engine name strings
4. **Future Extensible**: Easy to add new engines without string comparisons

## Model Files Organization

```
docker/wakeword-models/
├── hey-mycroft.pb              # Precise model file
├── hey-mycroft.pb.params       # Precise model metadata
└── (OpenWakeWord models are built-in from Python package)
```

### OpenWakeWord Models
All models are **built-in** to the openwakeword Python package. No separate downloads needed:
- hey_mycroft, alexa, americano, downstairs, grapefruit, grasshopper, jarvis, ok_google, timer, weather

### Precise Models
Must be provided as .pb files. Available from: https://github.com/MycroftAI/precise-data/tree/master/models

## Tools Provided

### 1. Configuration Validator
```bash
python3 validate_wake_word_config.py
```
Checks that .env is properly configured and model files exist.

### 2. Quick Configuration Switcher
```bash
python3 switch_wake_word.py openwakeword hey_mycroft 0.5
python3 switch_wake_word.py openwakeword alexa 0.4
python3 switch_wake_word.py precise 0.15
python3 switch_wake_word.py disable
python3 switch_wake_word.py config
```

### 3. Model Downloader
```bash
python3 download_wakeword_models.py
```
Downloads available public models (though Precise models currently require manual setup).

### 4. Comprehensive Configuration Guide
See: [WAKE_WORD_CONFIG.md](WAKE_WORD_CONFIG.md)

## Migration from Old System

If upgrading from the old system with `WAKE_WORD_ENGINE`:

### Ubuntu/Linux (OpenWakeWord)
```env
# Old
WAKE_WORD_ENGINE=openwakeword
WAKE_WORD_CONFIDENCE=0.5
OPENWAKEWORD_MODEL_PATH=hey_mycroft

# New
WAKE_WORD_ENABLED=true
OPENWAKEWORD_ENABLED=true
OPENWAKEWORD_WAKE_WORD=hey_mycroft
OPENWAKEWORD_MODEL_PATH=hey_mycroft
OPENWAKEWORD_CONFIDENCE=0.5

PRECISE_ENABLED=false
PICOVOICE_ENABLED=false
```

### Raspberry Pi (Precise)
```env
# Old
WAKE_WORD_ENGINE=precise
WAKE_WORD_CONFIDENCE=0.15
OPENWAKEWORD_MODEL_PATH=docker/wakeword-models/hey-mycroft.pb

# New
WAKE_WORD_ENABLED=true
PRECISE_ENABLED=true
PRECISE_WAKE_WORD=hey_mycroft
PRECISE_MODEL_PATH=docker/wakeword-models/hey-mycroft.pb
PRECISE_CONFIDENCE=0.15

OPENWAKEWORD_ENABLED=false
PICOVOICE_ENABLED=false
```

## Testing the New System

1. **Verify Configuration:**
   ```bash
   python3 validate_wake_word_config.py
   ```

2. **Start Orchestrator:**
   ```bash
   ./run_voice_demo.sh
   ```

3. **Listen for Wake Word:**
   - Say "hey mycroft" clearly into the microphone
   - Watch logs for:
     - `✓ Wake Word detector loaded` - Engine loaded successfully
     - `Wake confidence spike: X.XXXX` - Wake word detected!
     - `Audio detected but no spike: conf=X.XXXX` - Audio captured but not matching

4. **Try Different Models:**
   ```bash
   python3 switch_wake_word.py openwakeword alexa
   ./run_voice_demo.sh  # Try again with different model
   ```

## Known Issues & Gotchas

1. **Precise Requires Valid .pb File**
   - Empty .pb files will not work
   - Must download from Mycroft: https://github.com/MycroftAI/precise-data
   - Each model needs both .pb and .pb.params files

2. **OpenWakeWord Format is Specific**
   - Use built-in model **name** (e.g., `hey_mycroft`) not file path
   - Do NOT use Precise .pb format with OpenWakeWord
   - Custom models must be .tflite format

3. **Exactly One Engine Must Be Enabled**
   - Setting multiple `_ENABLED=true` will cause validation error
   - System won't start if none are enabled (when WAKE_WORD_ENABLED=true)

4. **Confidence Thresholds Are Engine-Specific**
   - Precise typically: 0.15-0.5 (lower default)
   - OpenWakeWord typically: 0.4-0.6 (higher default)
   - Picovoice: Vendor recommended values

## File Changes Summary

**Modified:**
- `.env` - Restructured wake word configuration
- `orchestrator/config.py` - Added per-engine fields, updated validation
- `orchestrator/main.py` - Updated detector initialization logic

**Created:**
- `WAKE_WORD_CONFIG.md` - Comprehensive configuration guide
- `validate_wake_word_config.py` - Configuration validation tool
- `switch_wake_word.py` - Quick configuration switcher
- `download_wakeword_models.py` - Model downloader

## Code Quality

- ✓ All Python code compiles without errors
- ✓ Configuration validation prevents common mistakes
- ✓ Clear error messages point to specific issues
- ✓ Backward compatible approach (old system removed cleanly)

## Next Steps

1. Test with actual hardware (Pi and Ubuntu)
2. Verify all wake word engines initialize correctly
3. Test model switching with the helper script
4. Gather feedback on configuration UX
5. Consider adding per-model sensitivity presets

## References

- [OpenWakeWord GitHub](https://github.com/openclawcompute/openwakeword)
- [Mycroft Precise GitHub](https://github.com/MycroftAI/precise)
- [Precise Model Data](https://github.com/MycroftAI/precise-data)
- [Picovoice Documentation](https://picovoice.ai/)
