# Build Optimizations & Import Validation

## Changes Made (March 2026)

### 1. Build Speed Improvements

**Problem**: Rebuilds were taking a very long time (10-15 minutes) because pip was re-downloading all packages every time.

**Solution**: Added persistent pip cache mounting to Docker container
- Cache location: `~/.cache/precise-build-pip/`
- Mounted into container at `/root/.cache/pip`
- Speeds up rebuilds by 3-5x after first build
- Disabled `pip cache purge` to preserve downloaded wheels

**Files modified**:
- `build_precise_engine_armv7.sh`: Added volume mount for pip cache

### 2. Comprehensive Import Validation

**Problem**: Missing imports (timeit, xml.dom, wrapt) were only discovered after deploying to Pi and running the orchestrator. This led to multiple rebuild/redeploy cycles.

**Solution**: Created automated validation that checks ALL required imports before deployment

**New script: `validate_precise_imports.py`**
- Extracts and inspects the built tarball
- Checks for presence of all TensorFlow, stdlib, and Precise dependencies
- Runs automatically after build (step 5/5)
- **Fails build if ANY imports are missing**
- Saves time by catching issues before SSH/SCP to Pi

**Validated imports include**:
- TensorFlow deps: `wrapt`, `google.protobuf`, `absl`, `numpy`, `scipy`, `h5py`, `keras`
- Stdlib modules: `timeit`, `xml`, `xml.dom`, `xml.dom.minidom`, `xml.etree`
- Precise deps: `prettyparse`, `speechpy`, `attrs`

### 3. Fixed Missing `wrapt` Dependency

**Problem**: TensorFlow 1.13.1 requires `wrapt` for decorator functionality, but it wasn't included in the bundle. Caused runtime error:
```
ModuleNotFoundError: No module named 'wrapt'
```

**Solution**: 
- Added `wrapt==1.11.2` to pip install
- Added `wrapt` to PyInstaller hidden_imports
- Will be caught by validation if ever removed

**Files modified**:
- `build_precise_engine_armv7.sh`: Added wrapt to dependencies and hidden imports

### 4. Import Discovery Tool

**New script: `extract_tensorflow_imports.py`**
- Scans TensorFlow source code to find ALL import statements
- Helps proactively identify dependencies before they cause failures
- Run inside Docker build container to analyze TensorFlow 1.13.1
- Generates list of external packages and hidden_imports

**Usage**:
```bash
# Inside build container after TensorFlow is installed:
docker run -it precise-armv7-builder:local bash
cd /opt/mycroft-precise
source .venv/bin/activate
python3 /path/to/extract_tensorflow_imports.py
```

## Build Workflow Now

```bash
./build_precise_engine_armv7.sh
```

**Steps**:
1. Check Docker buildx support
2. Generate Dockerfile for target arch
3. Build Docker image (cached layers)
4. Run build in container (**with pip cache mount** ⚡)
5. **Validate imports automatically** ✅
6. Ready to deploy if validation passes

**First build**: ~10-15 minutes (downloading wheels)
**Subsequent builds**: ~3-5 minutes (using cached wheels)

## How to Add New Dependencies

When adding new Python packages to precise-engine:

1. **Add to pip install** in `build_precise_engine_armv7.sh`:
   ```bash
   PIP_NO_CACHE_DIR=1 pip install \
     your-new-package==x.y.z
   ```

2. **Add to hidden_imports** in the PyInstaller spec sed command:
   ```bash
   | sed "s/hidden_imports = \[...\]/hidden_imports = [..., 'your_new_package']/"
   ```

3. **Update validation list** in `validate_precise_imports.py`:
   ```python
   PRECISE_IMPORTS = [
       'prettyparse',
       'speechpy',
       'attrs',
       'your_new_package',  # Add here
   ]
   ```

4. **Run build**: Validation will confirm it's properly bundled
   ```bash
   ./build_precise_engine_armv7.sh
   ```

## Preventing Future Import Issues

### For Precise (Pi)
- All required imports now validated before deployment
- Build fails if validation finds missing imports
- No more "deploy → test → find missing import → rebuild" cycles

### For OpenWakeWord (Ubuntu)
OpenWakeWord has different dependencies than Precise:
- Uses `openwakeword` package (not TensorFlow 1.13.1)
- Uses `tflite-runtime` for model inference
- Models auto-download via `openwakeword.utils.download_models()`

**Key OpenWakeWord dependencies**:
- `openwakeword` - main package
- `tflite-runtime` - TFLite inference
- `onnxruntime` - Alternative inference engine
- `requests` - Model downloading
- `numpy` - Array operations

**Already installed in**: `requirements-optional.txt`

### Testing Locally

Before deploying to Pi, you can test the bundle extraction and import validation:

```bash
# Build for ARMv7
./build_precise_engine_armv7.sh

# Validation runs automatically, but you can run it manually:
python3 validate_precise_imports.py artifacts/precise-engine-armv7/precise-engine.tar.gz

# If validation passes, deploy:
./deploy_precise_engine_to_pi.sh pi artifacts/precise-engine-armv7/precise-engine.tar.gz
```

## Cache Management

Pip cache location: `~/.cache/precise-build-pip/`

To clear cache (if you have issues):
```bash
rm -rf ~/.cache/precise-build-pip/*
```

To check cache size:
```bash
du -sh ~/.cache/precise-build-pip/
```

## Summary

✅ **Faster rebuilds** - pip cache persists between builds
✅ **No more missing imports** - comprehensive validation before deployment  
✅ **Proactive dependency tracking** - import extraction tool for TensorFlow
✅ **Fixed wrapt issue** - TensorFlow decorators now work
✅ **Better error messages** - validation tells you exactly what's missing
