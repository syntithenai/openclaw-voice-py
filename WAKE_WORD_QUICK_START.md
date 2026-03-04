# Wake Word Quick Start

## Current Configuration
✓ OpenWakeWord enabled with `hey_mycroft` model  
✓ Confidence threshold: 0.5  

## Try These Commands

### 1. Listen with Current Configuration
```bash
./run_voice_demo.sh
# Say: "hey mycroft"
# Look for: "Wake confidence spike:" in logs
```

### 2. Try Different Wake Words

**Alexa:**
```bash
python3 switch_wake_word.py openwakeword alexa
./run_voice_demo.sh
# Say: "alexa"
```

**Jarvis:**
```bash
python3 switch_wake_word.py openwakeword jarvis 
./run_voice_demo.sh
# Say: "hey jarvis"
```

**OK Google:**
```bash
python3 switch_wake_word.py openwakeword ok_google
./run_voice_demo.sh
# Say: "ok google"
```

### 3. Adjust Sensitivity

**More sensitive (more false positives):**
```bash
python3 switch_wake_word.py openwakeword hey_mycroft 0.3
./run_voice_demo.sh
```

**Less sensitive (more misses):**
```bash
python3 switch_wake_word.py openwakeword hey_mycroft 0.7
./run_voice_demo.sh
```

### 4. View Current Configuration
```bash
python3 switch_wake_word.py config
```

### 5. Validate Configuration
```bash
python3 validate_wake_word_config.py
```

## Available Wake Words

| Model | Demo Phrase |
|-------|------------|
| hey_mycroft | "hey mycroft" |
| alexa | "alexa" |
| jarvis | "hey jarvis" |
| ok_google | "ok google" |
| timer | "timer" (generic) |
| weather | "weather" (generic) |
| americano | "americano" (test word) |
| downstairs | "downstairs" (test word) |
| grapefruit | "grapefruit" (test word) |
| grasshopper | "grasshopper" (test word) |

## Troubleshooting

### Wake word not detected
1. Check audio is working: `python3 -m sounddevice`
2. Speak clearly and close to microphone
3. Lower confidence threshold to 0.3
4. Check logs: `tail -f orchestrator_output.log | grep -i wake`

### Too many false positives
1. Raise confidence threshold to 0.7
2. Try a different wake word model
3. Check for background noise

### Model file errors (Precise only)
1. Download valid .pb file from: https://github.com/MycroftAI/precise-data
2. Ensure file is not empty: `ls -lh docker/wakeword-models/*.pb`
3. Check .params file exists: `ls -lh docker/wakeword-models/*.pb.params`

## See Also
- [Full Configuration Guide](WAKE_WORD_CONFIG.md)
- [Implementation Details](WAKE_WORD_IMPLEMENTATION.md)
