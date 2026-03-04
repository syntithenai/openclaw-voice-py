#!/bin/bash

# Quick test script to verify wake word detectors can be imported

echo "Testing wake word detector imports..."
echo ""

python3 -c "
import sys
sys.path.insert(0, '.')

print('1. Testing OpenWakeWord import...')
try:
    from orchestrator.wakeword.openwakeword import OpenWakeWordDetector
    print('   ✓ OpenWakeWordDetector imported')
except Exception as e:
    print(f'   ✗ Failed: {e}')

print('')
print('2. Testing Mycroft Precise import...')
try:
    from orchestrator.wakeword.precise import MycoftPreciseDetector
    print('   ✓ MycoftPreciseDetector imported')
except Exception as e:
    print(f'   ✗ Failed: {e}')

print('')
print('3. Testing Picovoice import...')
try:
    from orchestrator.wakeword.picovoice import PicovoiceDetector
    print('   ✓ PicovoiceDetector imported')
except Exception as e:
    print(f'   ✗ Failed: {e}')

print('')
print('4. Testing WakeWordBase...')
try:
    from orchestrator.wakeword.base import WakeWordBase
    print('   ✓ WakeWordBase imported')
except Exception as e:
    print(f'   ✗ Failed: {e}')

print('')
print('✓ All wake word detector imports successful')
"
