#!/usr/bin/env python3
"""Validation script for native music backend integration."""

import sys
from pathlib import Path

# Add workspace to path
workspace = Path(__file__).parent
sys.path.insert(0, str(workspace))

def test_imports():
    """Test that required music imports and basic initialization work."""
    try:
        import asyncio
        from orchestrator.music import NativeMusicClientPool, MusicManager

        print("✓ NativeMusicClientPool import OK")
        print("✓ MusicManager import OK")

        async def _probe() -> None:
            pool = NativeMusicClientPool(pool_size=1, timeout=5.0)
            await pool.initialize()
            manager = MusicManager(pool)
            stats = await manager.get_stats()
            print(f"✓ Native music backend initialized (songs={stats.get('songs', '0')})")
            await pool.close()

        asyncio.run(_probe())
        
        return True
    except Exception as e:
        print(f"✗ Import test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_syntax():
    """Test syntax of modified files."""
    import py_compile
    files = [
        "orchestrator/main.py",
        "orchestrator/music/manager.py",
        "orchestrator/music/native_client.py",
        "orchestrator/music/native_player.py",
        "orchestrator/music/library_index.py",
        "orchestrator/music/playlist_store.py",
    ]
    
    for filepath in files:
        try:
            py_compile.compile(str(workspace / filepath), doraise=True)
            print(f"✓ {filepath} syntax OK")
        except py_compile.PyCompileError as e:
            print(f"✗ {filepath} syntax error: {e}")
            return False
    
    return True

if __name__ == "__main__":
    print("Validating native music backend integration...\n")
    
    success = True
    success = test_syntax() and success
    print()
    success = test_imports() and success
    
    if success:
        print("\n✓ All validation checks passed!")
        sys.exit(0)
    else:
        print("\n✗ Validation failed")
        sys.exit(1)
