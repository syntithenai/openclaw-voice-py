#!/usr/bin/env python3
"""Quick test of MPD orchestrator integration."""

import sys
import os
import time
import subprocess
from pathlib import Path

# Add workspace to path
workspace = Path(__file__).parent
sys.path.insert(0, str(workspace))

results = []

def log(msg):
    """Log to both stdout and results file."""
    print(msg, flush=True)
    results.append(msg)

# Test 1: Can we import MPDManager?
try:
    from orchestrator.services.mpd_manager import MPDManager
    log("✓ MPDManager import successful")
except Exception as e:
    log(f"✗ MPDManager import failed: {e}")
    sys.exit(1)

# Test 2: Can we instantiate it?
try:
    mgr = MPDManager()
    log("✓ MPDManager instantiation successful")
    log(f"  - Config path: {mgr.mpd_config_path or '(using default)'}")
except Exception as e:
    log(f"✗ MPDManager instantiation failed: {e}")
    sys.exit(1)

# Test 3: Is mpd binary available?
result = subprocess.run(['which', 'mpd'], capture_output=True, text=True)
if result.returncode == 0:
    log(f"✓ mpd binary found: {result.stdout.strip()}")
else:
    log("✗ mpd binary not found - install with: sudo apt install mpd")
    sys.exit(1)

# Test 4: Can we start MPD?
log("\n→ Attempting to start MPD...")
if mgr.start():
    log("✓ MPD start command succeeded")
    
    # Test 5: Is MPD ready?
    if mgr.wait_for_ready(timeout_sec=3):
        log("✓ MPD is ready and accepting connections")
        pid = mgr.get_pid()
        log(f"  - PID: {pid}")
        
        # Test 6: Can we stop it?
        if mgr.stop():
            log("✓ MPD stopped successfully")
        else:
            log("✗ MPD failed to stop")
    else:
        log("✗ MPD did not become ready within 3 seconds")
        mgr.stop()
else:
    log("✗ MPD failed to start")
    log("   Possible causes:")
    log("   - MPD not installed: sudo apt install mpd")
    log("   - Port 6600 already in use: sudo lsof -i :6600")
    log("   - Permission issues with MPD config directory")

# Summary
log("\n" + "="*50)
log("SUMMARY:")
log("="*50)
for result_line in results:
    if "✓" in result_line or "✗" in result_line or "→" in result_line:
        log(result_line)

# Write results to file
results_file = workspace / "MPD_TEST_RESULTS.txt"
with open(results_file, "w") as f:
    f.write("\n".join(results))
    f.write("\n")

log(f"\nResults saved to: {results_file}")
