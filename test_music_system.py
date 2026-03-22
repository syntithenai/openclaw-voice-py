#!/usr/bin/env python3
"""
Test script for music control system.

Tests native music client, manager, parser, and router functionality.
"""

import asyncio
import sys
from pathlib import Path
import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from orchestrator.music import NativeMusicClientPool, MusicManager, MusicFastPathParser, MusicRouter


@pytest.mark.asyncio
async def test_connection(host: str = "localhost", port: int = 6600):
    """Test basic music backend connection."""
    print(f"\n=== Testing Music Backend Connection to {host}:{port} ===")
    
    try:
        pool = NativeMusicClientPool(host, port, pool_size=1, timeout=5.0)
        await pool.initialize()
        print("✓ Connection successful")
        
        # Test basic command
        status = await pool.execute("status")
        print(f"✓ Music status: {status}")
        
        await pool.close()
        return True
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        return False


@pytest.mark.asyncio
async def test_manager(host: str = "localhost", port: int = 6600):
    """Test music manager operations."""
    print(f"\n=== Testing Music Manager ===")
    
    try:
        pool = NativeMusicClientPool(host, port, pool_size=3, timeout=5.0)
        await pool.initialize()
        
        manager = MusicManager(pool)
        
        # Test library stats
        stats = await manager.get_stats()
        songs = stats.get("songs", 0)
        albums = stats.get("albums", 0)
        artists = stats.get("artists", 0)
        print(f"✓ Library: {songs} songs, {albums} albums, {artists} artists")
        
        # Test status
        status = await manager.get_status()
        state = status.get("state", "unknown")
        print(f"✓ Player state: {state}")
        
        # Test current track (if playing)
        if state == "play":
            track = await manager.get_current_track()
            title = track.get("Title", "Unknown")
            artist = track.get("Artist", "Unknown")
            print(f"✓ Now playing: {title} by {artist}")
        
        await pool.close()
        return True
    except Exception as e:
        print(f"✗ Manager test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


@pytest.mark.asyncio
async def test_parser():
    """Test fast-path parser."""
    print(f"\n=== Testing Fast-Path Parser ===")
    
    parser = MusicFastPathParser()
    
    test_cases = [
        ("play", ("play", {})),
        ("pause", ("pause", {})),
        ("hey minecraft, stop playing.", ("stop", {})),
        ("next", ("next_track", {})),
        ("volume 50", ("set_volume", {"level": 50})),
        ("what's playing", ("get_current_track", {})),
        ("play some jazz", ("play_genre", {"genre": "jazz", "shuffle": True})),
        ("update library", ("update_library", {})),
        ("scan music", ("update_library", {})),
        ("play music by the beatles", ("play_artist", {"artist": "the beatles", "shuffle": True})),
    ]
    
    passed = 0
    failed = 0
    
    for text, expected in test_cases:
        result = parser.parse(text)
        if result == expected:
            print(f"✓ '{text}' -> {result}")
            passed += 1
        else:
            print(f"✗ '{text}' -> {result} (expected {expected})")
            failed += 1
    
    print(f"\nParser Tests: {passed} passed, {failed} failed")
    return failed == 0


@pytest.mark.asyncio
async def test_router(host: str = "localhost", port: int = 6600):
    """Test music router."""
    print(f"\n=== Testing Music Router ===")
    
    try:
        pool = NativeMusicClientPool(host, port, pool_size=3, timeout=5.0)
        await pool.initialize()
        
        manager = MusicManager(pool)
        router = MusicRouter(manager)
        
        # Test fast-path commands
        test_commands = [
            "what's playing",
            "pause",
            "play",
        ]
        
        for command in test_commands:
            response = await router.handle_request(command, use_fast_path=True)
            if response:
                print(f"✓ '{command}' -> {response}")
            else:
                print(f"  '{command}' -> (no fast-path match)")
        
        await pool.close()
        return True
    except Exception as e:
        print(f"✗ Router test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all tests."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Test music control system")
    parser.add_argument("--host", default="localhost", help="Backend host for compatibility client (default: localhost)")
    parser.add_argument("--port", type=int, default=6600, help="Backend port for compatibility client (default: 6600)")
    parser.add_argument("--test", choices=["all", "connection", "manager", "parser", "router"], 
                        default="all", help="Which test to run")
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("Music Control System Test Suite")
    print("=" * 70)
    
    results = {}
    
    # Parser test doesn't need a backend connection
    if args.test in ["all", "parser"]:
        results["parser"] = await test_parser()
    
    # Tests that need backend connection
    if args.test in ["all", "connection"]:
        results["connection"] = await test_connection(args.host, args.port)
    
    if args.test in ["all", "manager"]:
        results["manager"] = await test_manager(args.host, args.port)
    
    if args.test in ["all", "router"]:
        results["router"] = await test_router(args.host, args.port)
    
    # Summary
    print("\n" + "=" * 70)
    print("Test Summary")
    print("=" * 70)
    for test_name, passed in results.items():
        status = "✓ PASSED" if passed else "✗ FAILED"
        print(f"{test_name:20s} {status}")
    
    all_passed = all(results.values())
    print("=" * 70)
    if all_passed:
        print("✓ All tests passed!")
        return 0
    else:
        print("✗ Some tests failed")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
