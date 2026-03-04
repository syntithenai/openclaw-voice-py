#!/usr/bin/env python3
"""
Switch between wake word engines and models quickly.
Helps test different configurations without manual .env editing.
"""

import sys
import re
from pathlib import Path
from typing import Optional

def update_env(key: str, value: str) -> None:
    """Update a value in .env file."""
    env_path = Path(__file__).parent / ".env"
    
    # Read current content
    content = env_path.read_text()
    
    # Pattern to find and replace the key
    # Matches: KEY=value or # KEY=value (commented out)
    pattern = rf'^[#\s]*{key}=.*$'
    
    # Replace or add the key
    if re.search(pattern, content, re.MULTILINE):
        content = re.sub(
            pattern,
            f'{key}={value}',
            content,
            flags=re.MULTILINE
        )
    else:
        # Append to end of file
        if not content.endswith('\n'):
            content += '\n'
        content += f'{key}={value}\n'
    
    # Write back
    env_path.write_text(content)
    print(f"✓ Updated .env: {key}={value}")

def enable_openwakeword(model: str = "hey_mycroft", confidence: float = 0.5) -> None:
    """Enable OpenWakeWord engine."""
    print(f"\n→ Enabling OpenWakeWord engine with model: {model}")
    update_env("WAKE_WORD_ENABLED", "true")
    update_env("OPENWAKEWORD_ENABLED", "true")
    update_env("OPENWAKEWORD_WAKE_WORD", model)
    update_env("OPENWAKEWORD_MODEL_PATH", model)
    update_env("OPENWAKEWORD_CONFIDENCE", str(confidence))
    
    update_env("PRECISE_ENABLED", "false")
    update_env("PICOVOICE_ENABLED", "false")
    print("✓ OpenWakeWord enabled. Restart orchestrator to apply changes.")

def enable_precise(model_path: str = "docker/wakeword-models/hey-mycroft.pb", confidence: float = 0.15) -> None:
    """Enable Precise engine."""
    print(f"\n→ Enabling Precise engine with model: {model_path}")
    update_env("WAKE_WORD_ENABLED", "true")
    update_env("PRECISE_ENABLED", "true")
    update_env("PRECISE_WAKE_WORD", model_path.split('/')[-1].replace('.pb', ''))
    update_env("PRECISE_MODEL_PATH", model_path)
    update_env("PRECISE_CONFIDENCE", str(confidence))
    
    update_env("OPENWAKEWORD_ENABLED", "false")
    update_env("PICOVOICE_ENABLED", "false")
    print("✓ Precise enabled. Restart orchestrator to apply changes.")

def disable_wake_word() -> None:
    """Disable wake word detection."""
    print("\n→ Disabling wake word detection")
    update_env("WAKE_WORD_ENABLED", "false")
    print("✓ Wake word disabled. Restart orchestrator to apply changes.")

def list_models() -> None:
    """List available OpenWakeWord models."""
    print("\n" + "="*60)
    print("OPENWAKEWORD BUILT-IN MODELS")
    print("="*60)
    models = {
        "hey_mycroft": "Default - 'Hey Mycroft'",
        "alexa": "'Alexa'",
        "americano": "Random word",
        "downstairs": "Location keyword",
        "grapefruit": "Random word",
        "grasshopper": "Random word",
        "jarvis": "'Hey Jarvis'",
        "ok_google": "'OK Google'",
        "timer": "Generic keyword",
        "weather": "Generic keyword",
    }
    
    for model, description in models.items():
        print(f"  • {model:15} - {description}")

def show_current_config() -> None:
    """Show current wake word configuration."""
    env_path = Path(__file__).parent / ".env"
    content = env_path.read_text()
    
    print("\n" + "="*60)
    print("CURRENT WAKE WORD CONFIGURATION")
    print("="*60)
    
    # Extract wake word related settings
    for line in content.split('\n'):
        if any(key in line for key in [
            'WAKE_WORD_', 'PRECISE_', 'OPENWAKEWORD_', 'PICOVOICE_'
        ]) and not line.strip().startswith('#') and '=' in line:
            print(line)

def main():
    """Main menu."""
    if len(sys.argv) < 2:
        print("Wake Word Engine Configuration Helper")
        print("="*60)
        print("\nUsage: python3 switch_wake_word.py <command> [args]")
        print("\nCommands:")
        print("  openwakeword [model] [confidence]  - Enable OpenWakeWord")
        print("    Models: hey_mycroft, alexa, jarvis, ok_google, timer, weather, etc.")
        print("    Confidence: 0.0-1.0 (default: 0.5)")
        print("\n  precise [confidence]                - Enable Precise engine")
        print("    Confidence: 0.0-1.0 (default: 0.15)")
        print("\n  disable                             - Disable wake word detection")
        print("\n  list                                - List available models")
        print("\n  config                              - Show current configuration")
        print("\nExamples:")
        print("  python3 switch_wake_word.py openwakeword hey_mycroft 0.5")
        print("  python3 switch_wake_word.py openwakeword alexa 0.4")
        print("  python3 switch_wake_word.py precise 0.15")
        print("  python3 switch_wake_word.py disable")
        print("  python3 switch_wake_word.py list")
        print("  python3 switch_wake_word.py config")
        return
    
    command = sys.argv[1].lower()
    
    if command == "openwakeword":
        model = sys.argv[2] if len(sys.argv) > 2 else "hey_mycroft"
        confidence = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
        enable_openwakeword(model, confidence)
    
    elif command == "precise":
        confidence = float(sys.argv[2]) if len(sys.argv) > 2 else 0.15
        enable_precise(confidence=confidence)
    
    elif command == "disable":
        disable_wake_word()
    
    elif command == "list":
        list_models()
    
    elif command == "config":
        show_current_config()
    
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
    
    show_current_config()

if __name__ == "__main__":
    main()
