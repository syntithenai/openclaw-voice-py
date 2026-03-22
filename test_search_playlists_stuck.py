#!/usr/bin/env python3
"""Test to diagnose search and playlist loading issues."""

import asyncio
import logging
import time
import sys
import os
import pytest

# Add to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orchestrator.music import NativeMusicClientPool
from orchestrator.music.manager import MusicManager

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_music_operations():
    """Test search and playlist operations for bottlenecks."""
    
    # Connect to music backend
    pool = NativeMusicClientPool(
        host="127.0.0.1",
        port=6600,
        pool_size=6,  # Updated to new size
        timeout=5.0,
    )
    
    await pool.initialize()
    manager = MusicManager(pool)
    
    try:
        # Test 1: Measure playlist list time
        logger.info("=" * 60)
        logger.info("Test 1: List playlists")
        logger.info("=" * 60)
        start = time.monotonic()
        playlists = await manager.list_playlists()
        elapsed = time.monotonic() - start
        logger.info(f"✓ Listed {len(playlists)} playlists in {elapsed:.3f}s")
        for p in playlists[:5]:
            logger.info(f"  - {p}")
        
        # Test 2: Search with multiple queries concurrently
        logger.info("\n" + "=" * 60)
        logger.info("Test 2: Concurrent searches")
        logger.info("=" * 60)
        queries = ["love", "help", "music", "rock"]
        
        async def search_one(q):
            logger.info(f"  Starting search for '{q}'...")
            start = time.monotonic()
            try:
                results = await manager.search_library_for_ui(q, limit=50)
                elapsed = time.monotonic() - start
                logger.info(f"  ✓ Search '{q}': {len(results)} results in {elapsed:.3f}s")
                return len(results)
            except Exception as e:
                elapsed = time.monotonic() - start
                logger.error(f"  ✗ Search '{q}' failed after {elapsed:.3f}s: {e}")
                return 0
        
        start_all = time.monotonic()
        results_counts = await asyncio.gather(*[search_one(q) for q in queries])
        elapsed_all = time.monotonic() - start_all
        logger.info(f"✓ All {len(queries)} searches completed in {elapsed_all:.3f}s total")
        logger.info(f"  Results: {results_counts}")
        
        # Test 3: Check FTS index status
        logger.info("\n" + "=" * 60)
        logger.info("Test 3: FTS Index Status")
        logger.info("=" * 60)
        logger.info(f"  FTS Ready: {manager._fts_ready}")
        logger.info(f"  FTS Building: {manager._fts_building}")
        logger.info(f"  FTS Last Indexed: {manager._fts_last_indexed_count}")
        logger.info(f"  FTS Rebuild Task: {manager._fts_rebuild_task}")
        if manager._fts_rebuild_task and not manager._fts_rebuild_task.done():
            logger.warning("  ⚠ FTS rebuild is ongoing!")
        
        # Test 4: Get stats
        logger.info("\n" + "=" * 60)
        logger.info("Test 4: Library Stats")
        logger.info("=" * 60)
        start = time.monotonic()
        stats = await manager.get_stats()
        elapsed = time.monotonic() - start
        logger.info(f"✓ Got stats in {elapsed:.3f}s:")
        logger.info(f"  Songs: {stats.get('songs', 0)}")
        logger.info(f"  DB Update: {stats.get('db_update', 0)}")
        logger.info(f"  Playtime: {stats.get('playtime', 0)}")
        
        # Test 5: Get queue
        logger.info("\n" + "=" * 60)
        logger.info("Test 5: Get Queue")
        logger.info("=" * 60)
        start = time.monotonic()
        queue = await manager.get_queue(limit=100)
        elapsed = time.monotonic() - start
        logger.info(f"✓ Got {len(queue)} queue items in {elapsed:.3f}s")
        
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(test_music_operations())
