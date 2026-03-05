#!/usr/bin/env python3
"""
Download publicly available wake word models for Precise and OpenWakeWord engines.
Precise models: Mycroft Precise v0.3.0
OpenWakeWord models: Apache 2.0 licensed
"""

import os
import sys
import urllib.request
import tarfile
import gzip
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Public Precise models from Mycroft community
PRECISE_MODELS = {
    "hey_mycroft": "https://raw.githubusercontent.com/MycroftAI/precise-data/models/hey-mycroft.pb",
    "alexa": "https://raw.githubusercontent.com/MycroftAI/precise-data/models/alexa.pb",
    "jarvis": "https://raw.githubusercontent.com/MycroftAI/precise-data/models/jarvis.pb",
    "ok_google": "https://raw.githubusercontent.com/MycroftAI/precise-data/models/ok-google.pb",
    "hey_siri": "https://raw.githubusercontent.com/MycroftAI/precise-data/models/siri.pb",
}

# Precise model params files (required metadata)
PRECISE_PARAMS = {
    "hey_mycroft": "https://raw.githubusercontent.com/MycroftAI/precise-data/models/hey-mycroft.pb.params",
    "alexa": "https://raw.githubusercontent.com/MycroftAI/precise-data/models/alexa.pb.params",
    "jarvis": "https://raw.githubusercontent.com/MycroftAI/precise-data/models/jarvis.pb.params",
    "ok_google": "https://raw.githubusercontent.com/MycroftAI/precise-data/models/ok-google.pb.params",
    "hey_siri": "https://raw.githubusercontent.com/MycroftAI/precise-data/models/siri.pb.params",
}

# OpenWakeWord models (built-in, but can be cached as .tflite files)
# These are available from the OpenWakeWord repository and are included in the package
OPENWAKEWORD_MODELS_INFO = {
    "hey_mycroft": "Built-in model - no download needed",
    "alexa": "Built-in model - no download needed",
    "americano": "Built-in model - no download needed",
    "downstairs": "Built-in model - no download needed",
    "grapefruit": "Built-in model - no download needed",
    "grasshopper": "Built-in model - no download needed",
    "jarvis": "Built-in model - no download needed",
    "ok_google": "Built-in model - no download needed",
    "timer": "Built-in model - no download needed",
    "weather": "Built-in model - no download needed",
}

def download_file(url: str, dest_path: str) -> bool:
    """Download file from URL to destination path."""
    try:
        logger.info(f"Downloading: {url}")
        urllib.request.urlretrieve(url, dest_path)
        logger.info(f"✓ Saved to: {dest_path}")
        return True
    except Exception as e:
        logger.error(f"✗ Failed to download {url}: {e}")
        return False

def setup_precise_models(models_dir: str) -> None:
    """Download Precise models and params files."""
    logger.info("=" * 70)
    logger.info("Setting up Mycroft Precise v0.3.0 Models")
    logger.info("=" * 70)
    
    models_path = Path(models_dir)
    models_path.mkdir(parents=True, exist_ok=True)
    
    for model_name, url in PRECISE_MODELS.items():
        try:
            # Model file
            model_file = models_path / f"{model_name}.pb"
            if (not model_file.exists()) or model_file.stat().st_size == 0:
                if download_file(url, str(model_file)):
                    logger.info(f"  Model: {model_name}")
                else:
                    continue
            else:
                logger.info(f"  ✓ {model_name}.pb exists")
            
            # Params file
            params_url = PRECISE_PARAMS.get(model_name)
            if params_url:
                params_file = models_path / f"{model_name}.pb.params"
                if (not params_file.exists()) or params_file.stat().st_size == 0:
                    if download_file(params_url, str(params_file)):
                        logger.info(f"  Params: {model_name}.pb.params")
                    else:
                        logger.warning(f"  Could not download params for {model_name}")
                else:
                    logger.info(f"  ✓ {model_name}.pb.params exists")
        except Exception as e:
            logger.error(f"  Error setting up {model_name}: {e}")

def setup_openwakeword_models() -> None:
    """List OpenWakeWord models (they're built-in, no download needed)."""
    logger.info("=" * 70)
    logger.info("OpenWakeWord Models Available")
    logger.info("=" * 70)
    logger.info("OpenWakeWord models are built-in to the package.")
    logger.info("Available models (use as model name in config):")
    for model_name, info in OPENWAKEWORD_MODELS_INFO.items():
        logger.info(f"  • {model_name:15} - {info}")
    logger.info("")
    logger.info("Usage: Set OPENWAKEWORD_WAKE_WORD=<model_name> in .env")

def main():
    """Main setup routine."""
    logger.info("Starting wake word model setup...")
    
    # Get repository root
    repo_root = Path(__file__).parent.resolve()
    models_dir = repo_root / "docker" / "wakeword-models"
    
    # Setup Precise models
    setup_precise_models(str(models_dir))
    
    # Setup OpenWakeWord models
    setup_openwakeword_models()
    
    logger.info("=" * 70)
    logger.info("Setup Complete!")
    logger.info("=" * 70)
    logger.info("\nNext steps:")
    logger.info("1. Update .env with wake word engine configuration:")
    logger.info("   PRECISE_ENABLED=true/false")
    logger.info("   PRECISE_MODEL=<model_name>")
    logger.info("   OPENWAKEWORD_ENABLED=true/false")
    logger.info("   OPENWAKEWORD_WAKE_WORD=<model_name>")
    logger.info("2. Restart orchestrator: ./run_orchestrator.sh")

if __name__ == "__main__":
    main()
