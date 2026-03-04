# Mycroft Precise Model Setup Guide

Since `mycroft-precise-runner` has been successfully installed on your Raspberry Pi, you now need to download a wake word model.

## Quick Start

The `setup_wakeword.sh` script attempts automatic download, but if that fails, follow these manual steps:

### Option 1: Download from Your Development Machine (Recommended)

**On your host development machine:**

```bash
# Download a model file (choose one)
mkdir -p ~/downloads/precise-models

# Hey Mycroft (recommended)
wget https://github.com/MycroftAI/precise-data/raw/master/models/hey-mycroft.pb \
  -O ~/downloads/precise-models/hey-mycroft.pb

# Or Hey Jarvis
wget https://github.com/MycroftAI/precise-data/raw/master/models/jarvis.pb \
  -O ~/downloads/precise-models/jarvis.pb

# Copy to Raspberry Pi
scp ~/downloads/precise-models/*.pb pi:~/openclaw-voice/docker/wakeword-models/
```

### Option 2: Direct Download on Raspberry Pi

**SSH into the Pi:**

```bash
ssh pi
cd ~/openclaw-voice

# Create the models directory if it doesn't exist
mkdir -p docker/wakeword-models

# Download using Python (more reliable than wget on some networks)
. .venv_orchestrator/bin/activate
python3 << 'EOF'
import urllib.request
import os

models = {
    'hey-mycroft': 'https://github.com/MycroftAI/precise-data/raw/master/models/hey-mycroft.pb',
    'jarvis': 'https://github.com/MycroftAI/precise-data/raw/master/models/jarvis.pb',
}

os.makedirs('docker/wakeword-models', exist_ok=True)

for name, url in models.items():
    path = f'docker/wakeword-models/{name}.pb'
    if os.path.exists(path):
        print(f'✓ {path} already exists')
    else:
        try:
            print(f'Downloading {name}...')
            urllib.request.urlretrieve(url, path)
            print(f'✓ {path} ({os.path.getsize(path)} bytes)')
        except Exception as e:
            print(f'✗ Failed: {e}')
EOF
```

## Enable Precise in Your Configuration

Once you have a model file, update your `.env` file:

```env
WAKE_WORD_ENABLED=true
WAKE_WORD_ENGINE=precise
WAKE_WORD_CONFIDENCE=0.5
```

Then restart the orchestrator:

```bash
ssh pi
cd ~/openclaw-voice
./run_orchestrator.sh
```

## Available Models

- **hey-mycroft** - Default model, optimized for "Hey Mycroft" trigger phrase
- **jarvis** - For "Hey Jarvis" trigger phrase  
- **americano** - For "Americano" trigger phrase
- **timer** - For "Timer" wake word
- **weather** - For "Weather" wake word

## Troubleshooting

### Model file not found
If you see: `FileNotFoundError: docker/wakeword-models/hey-mycroft.pb`

**Solution:** Download the model file manually using the steps above.

### Detector fails to initialize
If you see: `Mycroft Precise initialization failed`

**Solution:** Check that:
1. The model file exists and is readable
2. The path in `OPENWAKEWORD_MODEL_PATH` is correct (must match where the file is located)
3. The file is not corrupted (check file size is > 1MB)

### False positives or missed detections
Adjust `WAKE_WORD_CONFIDENCE` (0.0-1.0):
- **Higher values** (0.7-0.9): Fewer false positives, may miss some detections
- **Lower values** (0.3-0.5): More sensitive, may have more false positives

## Testing

Once configured, test with:

```bash
# SSH into Pi and tail the logs
ssh pi "cd ~/openclaw-voice && tail -f orchestrator_output.log"

# In another terminal, speak the wake word into the microphone
# You should see detection logs
```

## Technical Details

- **Package:** `precise-runner` (0.3.1+)
- **Dependencies:** PyAudio (included automatically)
- **Python Version:** 3.11 (on Raspberry Pi)
- **Architecture:** ARMv7 (supported, unlike openwakeword)
- **Model Format:** TensorFlow Lite (.pb files, typically 0.5-2.0 MB)

## References

- [Mycroft Precise GitHub](https://github.com/MycroftAI/precise)
- [Precise Model Repository](https://github.com/MycroftAI/precise-data)
- [Piwheels precise-runner](https://www.piwheels.org/project/precise-runner/)
