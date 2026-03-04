# Precise Engine Compatibility Matrix & Release Checklist

## Compatibility Matrix

| Artifact | CPU Arch | Typical OS | Python ABI inside bundle | Expected loader | Status |
|---|---|---|---|---|---|
| `precise-engine-armv7.tar.gz` | ARMv7 (`armv7l`, 32-bit) | Raspberry Pi OS 32-bit (Debian-based) | CPython 3.7 (PyInstaller) | `/lib/ld-linux-armhf.so.3` | Recommended for Pi 3/4 on 32-bit OS |
| `precise-engine-arm64.tar.gz` | ARM64 (`aarch64`, 64-bit) | Raspberry Pi OS 64-bit / Ubuntu ARM64 | CPython 3.7 (PyInstaller) | `/lib/ld-linux-aarch64.so.1` | Experimental (requires external TF 1.13.1 aarch64 wheel) |

### Notes
- Artifacts are architecture-specific and **not interchangeable**.
- Bundle includes TensorFlow 1.13.1 + pinned dependencies and proactively bundled stdlib modules.
- Launcher sets `LD_LIBRARY_PATH` and `PYTHONPATH` to prefer bundle contents.
- ARM64 build requires `TF113_AARCH64_WHL_URL` because TensorFlow 1.13.1 is not available in default package indexes for aarch64.
- Always validate on target with:
  - `precise-engine --version`
  - a model smoke test (e.g., `docker/wakeword-models/hey-mycroft.pb`).

---

## Release Checklist

### Pre-build
- [ ] `docker buildx` available locally or in CI
- [ ] QEMU/binfmt configured for cross-build
- [ ] Wakeword model file exists in repo (`docker/wakeword-models/hey-mycroft.pb`)
- [ ] Build scripts are executable (`build_precise_engine_armv7.sh`, `build_precise_engine_arm64.sh`)

### Build
- [ ] Build ARMv7 artifact
  - `./build_precise_engine_armv7.sh`
- [ ] Build ARM64 artifact
  - `TF113_AARCH64_WHL_URL=<wheel-url> ./build_precise_engine_arm64.sh`
- [ ] Confirm generated files:
  - `artifacts/precise-engine-armv7/precise-engine.tar.gz`
  - `artifacts/precise-engine-arm64/precise-engine.tar.gz`

### Validation
- [ ] Deploy each artifact to representative target hardware/OS
- [ ] Verify launcher + binary:
  - [ ] `precise-engine --version` returns `0.3.0`
  - [ ] No `ModuleNotFoundError` in startup logs
- [ ] Verify orchestrator wake-word startup logs show detector initialized and process running

### Publish
- [ ] Tag release (or run manual workflow dispatch)
- [ ] CI workflow `Release Precise Engine Artifacts` succeeds for armv7 + arm64
- [ ] GitHub release contains assets:
  - `precise-engine-armv7.tar.gz`
  - `precise-engine-arm64.tar.gz`
  - checksum files (`.md5`)

### Post-release
- [ ] Update release notes with tested Pi models/OS versions
- [ ] Announce any known limitations (audio device naming, model path assumptions)
