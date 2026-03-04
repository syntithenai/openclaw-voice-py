# Precise Model File - Installation Notes

## Status: ✅ Configured, ⏳ Awaiting Model File

Your orchestrator is now **configured to use Mycroft Precise** with the hey-mycroft wake word.

### Configuration Applied ✓
- **WAKE_WORD_ENABLED**: true
- **WAKE_WORD_ENGINE**: precise
- **WAKE_WORD_CONFIDENCE**: 0.5
- **Model Path**: docker/wakeword-models/hey-mycroft.pb

### What's Missing
The actual `.pb` model file needs to be downloaded. Currently, a placeholder exists at the path above.

### Getting the Model File

**Option 1: From GitHub Releases (Recommended)**
```bash
cd ~/openclaw-voice/docker/wakeword-models
wget https://github.com/MycroftAI/precise/releases/download/v0.3.1/hey-mycroft.pb
```

**Option 2: If Release Download Fails**
Try alternative sources:
```bash
# Alternative GitHub raw URL
curl -L https://raw.githubusercontent.com/MycroftAI/precise-data/master/models/hey-mycroft.pb -o hey-mycroft.pb

# Or download on host machine and copy
scp hey-mycroft.pb pi:~/openclaw-voice/docker/wakeword-models/
```

**Option 3: Manual Download**
1. Visit: https://github.com/MycroftAI/precise/releases
2. Find the model files (hey-mycroft.pb, etc)
3. Download and copy to your Pi at: `~/openclaw-voice/docker/wakeword-models/`

### Verify Model Installation
Once downloaded, verify the file exists and is not the placeholder:
```bash
ssh pi "ls -lh ~/openclaw-voice/docker/wakeword-models/hey-mycroft.pb"
# Should show a file > 1MB (not a text file)
```

### Alternative: Use without Model
If you can't download the model, you can temporarily:
1. Keep WAKE_WORD_ENABLED=false in .env
2. Or switch to different engine with available models

### After Getting the Model
1. Replace the placeholder at `docker/wakeword-models/hey-mycroft.pb`
2. The orchestrator will automatically load it on restart
3. Test with: "Hey Mycroft" spoken near the microphone
4. Check logs: `tail -f ~/openclaw-voice/orchestrator_output.log`

---
**Note**: The model file is a TensorFlow Lite binary (~2-3 MB) that enables wake word detection.
