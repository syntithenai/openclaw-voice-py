#!/usr/bin/env python3
"""
Validate wake word engine configuration.
Checks that .env is properly configured and model files exist.
"""

import sys
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def check_config():
    """Validate wake word configuration."""
    repo_root = Path(__file__).parent.resolve()
    
    # Import config to trigger validation
    sys.path.insert(0, str(repo_root))
    try:
        from orchestrator.config import VoiceConfig
    except ImportError as e:
        logger.error(f"Could not import config: {e}")
        return False
    
    try:
        config = VoiceConfig()
    except ValueError as e:
        logger.error(f"Configuration validation failed: {e}")
        return False
    
    if not config.wake_word_enabled:
        logger.info("Wake word detection is disabled (WAKE_WORD_ENABLED=false)")
        return True
    
    # Check that exactly one engine is enabled
    engines_enabled = sum([
        config.precise_enabled,
        config.openwakeword_enabled,
        config.picovoice_enabled
    ])
    
    if engines_enabled == 0:
        logger.error("No wake word engine is enabled")
        return False
    elif engines_enabled > 1:
        logger.error("Multiple wake word engines are enabled (set only one)")
        return False
    
    # Validate selected engine
    if config.openwakeword_enabled:
        logger.info("✓ OpenWakeWord engine selected")
        logger.info(f"  Wake word: {config.openwakeword_wake_word}")
        logger.info(f"  Model: {config.openwakeword_model_path}")
        logger.info(f"  Confidence threshold: {config.openwakeword_confidence}")
        
        # OpenWakeWord models are built-in, just verify settings
        if not config.openwakeword_model_path:
            logger.error("  ✗ OPENWAKEWORD_MODEL_PATH is empty")
            return False
        logger.info("  ✓ Configuration looks good")
    
    elif config.precise_enabled:
        logger.info("✓ Precise engine selected")
        logger.info(f"  Wake word: {config.precise_wake_word}")
        logger.info(f"  Model: {config.precise_model_path}")
        logger.info(f"  Confidence threshold: {config.precise_confidence}")
        
        # Check that model file exists
        model_path = repo_root / config.precise_model_path
        if not model_path.exists():
            logger.error(f"  ✗ Model file not found: {model_path}")
            return False
        
        model_size = model_path.stat().st_size
        if model_size == 0:
            logger.error(f"  ✗ Model file is empty: {model_path}")
            logger.info("  → Download a Precise model: https://github.com/MycroftAI/precise-data/tree/master/models")
            return False
        elif model_size < 10000:
            logger.warning(f"  ⚠ Model file is very small ({model_size} bytes), may be invalid")
        
        logger.info(f"  ✓ Model file exists ({model_size} bytes)")
        
        # Check for .params file
        params_path = repo_root / f"{config.precise_model_path}.params"
        if not params_path.exists():
            logger.warning(f"  ⚠ Model params file not found: {params_path}")
            logger.info("    (Precise may still work, but metadata is missing)")
        else:
            logger.info(f"  ✓ Model params file exists")
    
    elif config.picovoice_enabled:
        logger.info("✓ Picovoice engine selected")
        logger.info(f"  Wake word: {config.picovoice_wake_word}")
        logger.info(f"  Confidence threshold: {config.picovoice_confidence}")
        
        if not config.picovoice_key:
            logger.error("  ✗ PICOVOICE_KEY is empty")
            return False
        logger.info("  ✓ API key is configured")
    
    logger.info("\n✓ Wake word configuration is valid!")
    return True

if __name__ == "__main__":
    success = check_config()
    sys.exit(0 if success else 1)
