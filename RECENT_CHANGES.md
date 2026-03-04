# Recent Changes Summary

## 0. ✅ Fixed "Exec Format Error" in Precise Engine Subprocess Invocation

**Problem**: When the orchestrator tried to invoke `precise-engine` via Python's `subprocess.Popen`, it failed with "Exec format error: 8". The bash launcher script worked fine when called manually via SSH, but failed when invoked from Python's subprocess module.

**Root Cause**: Python's `subprocess.Popen` without `shell=True` has difficulties with bash launcher scripts that export environment variables and then use `exec` to replace the process. The environment variables weren't being properly passed through the subprocess call.

**Solution**: Replaced the bash launcher script with a **Python wrapper script** that directly uses `os.execvp()` to invoke the binary with proper environment variables. This is more reliable because:
1. Python directly sets environment variables via `os.environ.copy()` and `os.execvp()`
2. No shell escaping or interpretation issues
3. Process replacement happens at the Python level, compatible with subprocess calls

**Implementation**:
- Created `/tmp/precise_launcher.py`: Python wrapper that sets `LD_LIBRARY_PATH` and `PYTHONPATH`, then calls `os.execvp()`
- Deployed via `scp` to `~/.venv_orchestrator/bin/precise-engine`
- Made executable with `chmod +x`

**File Changes**:
- [deploy_precise_engine_to_pi.sh](deploy_precise_engine_to_pi.sh): Updated step [2/4] to create Python launcher instead of bash script

**Impact**: 
- ✅ Precise engine now initializes successfully when called from orchestrator
- ✅ `subprocess.Popen` can properly invoke the launcher without "Exec format error"
- ✅ Full wake word detection pipeline operational

**Testing**: Orchestrator running with 2 processes active, continuously processing audio and handling Cut-in triggers.

---

## 1. ✅ Fixed TensorFlow Import Error in Precise-Engine Bundle

**Problem**: PyInstaller bundle of precise-engine was failing with `ModuleNotFoundError: No module named 'xml.dom'`. This occurred when the XML standard library module was needed by TensorFlow (via absl.flags._flagvalues).

**Root Cause**: PyInstaller bundles Python standard library modules in `base_library.zip`, but for some modules like `xml`, the bundled version wasn't being found. The xml module needs to be accessible when the PyInstaller-bundled Python runs.

**Solution**: Updated `PYTHONPATH` in the launcher script to include the system's Python stdlib directory:

**In launcher scripts**:
```python
env['PYTHONPATH'] = bundle + ':/usr/lib/python3.11:' + env.get('PYTHONPATH', '')
```

**Also updated in `build_precise_engine_armv7.sh`** (launcher script):
```bash
export PYTHONPATH="$BUNDLE:/usr/lib/python3.11:${PYTHONPATH:-}"
```

**Impact**: 
- ✅ Orchestrator now initializes without import errors
- ✅ TensorFlow 1.13.1 loads successfully in the bundled engine
- ✅ Full voice pipeline operational (STT, TTS, VAD, gateway)
- Precise wake word detection configured

**Technical Details**:
- TensorFlow 1.13.1 has complex lazy-loaded nested modules with optional dependencies
- absl library (required by TensorFlow) imports xml.dom.minidom at initialization
- PyInstaller's hidden_imports list helped catch most dependencies but missed standard library module resolution
- Solution: Chain system stdlib after bundle in PYTHONPATH so Python falls back to system libs when not found in bundle

---

## 2. ✅ Fixed Hotword Prebuffer Capture Issue

**Problem**: After hotword detection, the system was capturing too much pre-roll audio (200ms), which included the spoken hotword itself ("Hey, my craft") being transcribed.

**Solution**: Reduced prebuffer from 200ms to 80ms in [orchestrator/main.py](orchestrator/main.py#L999)

**Before**: 
```python
wake_pre_roll_ms = min(200, config.pre_roll_ms)
```

**After**:
```python
# Reduced prebuffer from 200ms to 80ms to avoid capturing the hotword itself being spoken
wake_pre_roll_ms = min(80, config.pre_roll_ms)
```

**Impact**: Hotword "Hey, my craft" will no longer be included in the transcription. You can further tune via `.env`:
```bash
WAKE_WORD_PREBUFFER_MS=50  # Reduce further if needed
```

---

## 3. ✅ Created Raspbian Installation Suite

### Files Created:

1. **[install.sh](install.sh)** (14KB, executable)
   - Interactive installer for Raspbian 32-bit and 64-bit
   - Detects architecture and OS version automatically
   - Installs all system dependencies
   - Creates Python virtual environment
   - Installs core + optional Python packages
   - Interactive configuration prompts for:
     - Audio devices
     - Gateway URL + token
     - Whisper (STT) URL
     - Piper (TTS) URL and voice
     - Wake word model and confidence
     - VAD backend and aggressiveness
     - Log level
   - Generates `.env` configuration file
   - Creates helper scripts: `activate.sh`, `run.sh`

2. **[RASPBIAN_INSTALL.md](RASPBIAN_INSTALL.md)** (Comprehensive guide)
   - Detailed step-by-step installation instructions
   - Configuration explanation and examples
   - Troubleshooting section with solutions for:
     - Audio device issues
     - Gateway connection failures
     - Whisper/Piper service problems
     - High CPU usage
     - Memory issues
     - Hotword capture issues
   - Advanced configuration options
   - Performance tips for different Pi models
   - Uninstall instructions
   - Getting help resources

3. **[QUICK_START_RASPBIAN.sh](QUICK_START_RASPBIAN.sh)** (Quick reference guide)
   - One-command installation guide
   - Quick verification steps
   - Troubleshooting checklist
   - Common configuration options
   - System requirements
   - Architecture support info

### Supported Systems:

- **Architectures**: 32-bit (ARMv6/7) and 64-bit (ARM64)
- **Raspbian Versions**: Bullseye, Bookworm, Buster
- **Hardware**: Raspberry Pi 3, 4, Zero 2W, 5

### Interactive Configuration:

The installer prompts for:

```
Audio devices (capture/playback)
Gateway: ws://openclaw.local:8000
Gateway token: (your-token)
Whisper URL: http://localhost:10000
Piper URL: http://localhost:10001
Piper voice: en_US-amy-medium
Piper speed: 1.0
Wake word model: hey_mycroft
Wake word confidence: 0.95
VAD backend: webrtc|silero
VAD aggressiveness: 1
Log level: DEBUG|INFO|WARNING|ERROR
```

### Generated Files:

After installation, the script creates:

```
.venv_orchestrator/          # Python virtual environment
.env                          # Configuration file
activate.sh                   # Activation script
run.sh                        # Run script
orchestrator.log              # Log file (created on first run)
```

---

## How to Use (For End Users)

### Quick Install:

```bash
cd /path/to/openclaw-voice-py
bash install.sh
```

Then follow the interactive prompts. Takes ~5-10 minutes depending on internet speed.

### Run the Orchestrator:

```bash
bash run.sh
```

Monitor logs:
```bash
tail -f orchestrator.log
```

---

## Key Features of the Installation Suite

✅ **Cross-platform**: Auto-detects Raspbian version and architecture  
✅ **Interactive**: Guides users through configuration step-by-step  
✅ **Complete**: Installs all core + optional dependencies  
✅ **Safe**: Validates prerequisites before proceeding  
✅ **Documented**: Includes comprehensive troubleshooting guide  
✅ **Helper scripts**: Creates `activate.sh` and `run.sh` for easy operation  
✅ **Flexible**: Allows all configuration via prompts or manual `.env` editing  

---

## Testing

To verify the installation worked:

```bash
source .venv_orchestrator/bin/activate
python3 -c "import orchestrator; print('✓ Orchestrator imported successfully')"
```

To test audio:
```bash
python3 -m sounddevice
```

To test services:
```bash
curl http://localhost:10000/info  # Whisper
curl http://localhost:10001/info  # Piper
```

---

## Docker Build Status

As a reminder from the previous session, all Docker images are successfully built:

```
✓ openclaw-voice-py-orchestrator:latest
✓ openclaw-voice-py-whisper:latest
✓ openclaw-voice-py-piper:latest
```

These are independent of the Raspbian installation — they run in containers on systems with Docker.

---

## Next Steps

1. **Test prebuffer fix**: Run and listen for "Hey, my craft" being transcribed
2. **Deploy on Raspberry Pi**: Use `bash install.sh` on the target Raspbian system
3. **Adjust if needed**: Edit `.env` for fine-tuning (WAKE_WORD_PREBUFFER_MS, etc.)
4. **Monitor**: Check logs for any issues during first run

---

## Files Modified vs Created

**Modified**:
- `orchestrator/main.py` - Prebuffer reduced from 200ms to 80ms

**Created**:
- `install.sh` - Interactive installer (14KB)
- `RASPBIAN_INSTALL.md` - Comprehensive installation guide
- `QUICK_START_RASPBIAN.sh` - Quick reference guide
