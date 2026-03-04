#!/usr/bin/env python3
"""
Validate that all required imports are available in the precise-engine bundle.
Run this after building but before deploying to catch missing dependencies.

Usage:
    python3 validate_precise_imports.py artifacts/precise-engine-armv7/precise-engine.tar.gz
"""

import sys
import os
import tarfile
import tempfile
import shutil
from pathlib import Path
import subprocess
import ast


# Comprehensive list of imports TensorFlow 1.13.1 requires
TENSORFLOW_IMPORTS = [
    'wrapt',
    'gast',
    'astor',
    'google.protobuf',
    'absl',
    'absl.flags',
    'absl.app',
    'numpy',
    'scipy',
    'scipy.signal',
    'h5py',
    'keras',
]

# Standard library modules that PyInstaller often misses
STDLIB_IMPORTS = [
    'timeit',
    'fractions',
    'xml',
    'xml.dom',
    'xml.dom.minidom',
    'xml.etree',
    'xml.etree.ElementTree',
]

# Precise-specific runtime imports
PRECISE_IMPORTS = [
    'prettyparse',
    'speechpy',
    'attrs',
]

ALL_IMPORTS = TENSORFLOW_IMPORTS + STDLIB_IMPORTS + PRECISE_IMPORTS


EXTERNAL_IMPORT_IGNORE_PREFIXES = (
    'tensorflow',
    'tensorflow_core',
    'precise',
)


def _is_stdlib_module(module_name):
    """Best-effort check for stdlib modules."""
    stdlib_names = getattr(sys, 'stdlib_module_names', None)
    if stdlib_names is not None:
        return module_name in stdlib_names

    # Fallback for older Python versions
    try:
        import importlib.util
        spec = importlib.util.find_spec(module_name)
        if spec is None or spec.origin is None:
            return False
        return 'site-packages' not in spec.origin and 'dist-packages' not in spec.origin
    except Exception:
        return False


def discover_tensorflow_external_imports(dist_dir):
    """Discover third-party imports used by tensorflow autograph code in the bundle."""
    autograph_dir = dist_dir / 'tensorflow_core' / 'python' / 'autograph'
    if not autograph_dir.exists():
        return []

    discovered = set()
    for py_file in autograph_dir.rglob('*.py'):
        try:
            source = py_file.read_text(encoding='utf-8')
            tree = ast.parse(source)
        except Exception:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split('.')[0]
                    if top and not top.startswith('_'):
                        discovered.add(top)
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    continue
                if node.module:
                    top = node.module.split('.')[0]
                    if top and not top.startswith('_'):
                        discovered.add(top)

    filtered = []
    for mod in sorted(discovered):
        if any(mod.startswith(prefix) for prefix in EXTERNAL_IMPORT_IGNORE_PREFIXES):
            continue
        if _is_stdlib_module(mod):
            continue
        filtered.append(mod)

    return filtered


def extract_tarball(tarball_path, extract_to):
    """Extract precise-engine.tar.gz to a temporary directory."""
    print(f"Extracting {tarball_path}...")
    with tarfile.open(tarball_path, 'r:gz') as tar:
        tar.extractall(extract_to)
    
    # Find the precise-engine or precise-engine.dist directory
    dist_dirs = list(Path(extract_to).glob('**/precise-engine'))
    if not dist_dirs:
        dist_dirs = list(Path(extract_to).glob('**/precise-engine.dist'))
    if not dist_dirs:
        raise FileNotFoundError("Could not find precise-engine or precise-engine.dist in tarball")
    
    return dist_dirs[0]


def check_import_in_bundle(dist_dir, import_name):
    """Check if an import is available in the bundle."""
    # Check in base_library.zip
    base_lib = dist_dir / 'base_library.zip'
    if base_lib.exists():
        try:
            result = subprocess.run(
                ['python3', '-m', 'zipfile', '-l', str(base_lib)],
                capture_output=True,
                text=True,
                timeout=5
            )
            module_path = import_name.replace('.', '/')
            if module_path in result.stdout or f'{module_path}.py' in result.stdout:
                return True, 'base_library.zip'
        except Exception as e:
            print(f"  Warning: Could not check base_library.zip: {e}")
    
    # Check in dist directory (as .py or .so files or as a directory)
    module_parts = import_name.split('.')
    
    # Look for the top-level module
    top_module = module_parts[0]
    
    # Check for .py file
    py_file = dist_dir / f'{top_module}.py'
    if py_file.exists():
        return True, str(py_file.relative_to(dist_dir))
    
    # Check for .so file (compiled extension)
    so_files = list(dist_dir.glob(f'{top_module}*.so'))
    if so_files:
        return True, str(so_files[0].relative_to(dist_dir))
    
    # Check for directory with __init__.py or .so files (compiled packages)
    module_dir = dist_dir / top_module
    if module_dir.is_dir():
        # For nested imports like 'xml.dom', check the full path
        check_path = module_dir
        for part in module_parts[1:]:
            check_path = check_path / part
        
        # Check if the nested path exists
        if len(module_parts) > 1:
            if check_path.is_dir() and (check_path / '__init__.py').exists():
                return True, str(check_path.relative_to(dist_dir))
            elif check_path.is_file() or (check_path.with_suffix('.py')).exists():
                return True, str(check_path.relative_to(dist_dir))
        
        # Top-level module dir exists with __init__.py, .py files, or .so files (compiled)
        has_py = (module_dir / '__init__.py').exists() or list(module_dir.glob('*.py'))
        has_so = list(module_dir.rglob('*.so'))  # Check recursively for compiled extensions
        if has_py or has_so:
            return True, str(module_dir.relative_to(dist_dir))
    
    return False, None


def validate_bundle(tarball_path):
    """Main validation function."""
    if not os.path.exists(tarball_path):
        print(f"ERROR: Tarball not found: {tarball_path}")
        return False
    
    print(f"\n{'='*70}")
    print(f"Validating precise-engine bundle: {tarball_path}")
    print(f"{'='*70}\n")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            dist_dir = extract_tarball(tarball_path, tmpdir)
            print(f"Found bundle: {dist_dir}\n")
            
            missing = []
            found = []

            dynamic_imports = discover_tensorflow_external_imports(dist_dir)
            if dynamic_imports:
                print("Discovered tensorflow autograph external imports:")
                for mod in dynamic_imports:
                    print(f"  - {mod}")
                print()

            required_imports = sorted(set(ALL_IMPORTS + dynamic_imports))
            
            print("Checking required imports:\n")
            
            for import_name in required_imports:
                available, location = check_import_in_bundle(dist_dir, import_name)
                
                if available:
                    found.append(import_name)
                    print(f"  ✓ {import_name:30s} -> {location}")
                else:
                    missing.append(import_name)
                    print(f"  ✗ {import_name:30s} -> MISSING")
            
            print(f"\n{'='*70}")
            print(f"Results: {len(found)}/{len(required_imports)} imports found")
            print(f"{'='*70}\n")
            
            if missing:
                print("❌ MISSING IMPORTS:")
                for mod in missing:
                    print(f"   - {mod}")
                print(f"\nAction required:")
                print(f"  1. Add missing modules to pip install in build script")
                print(f"  2. Add to hidden_imports in PyInstaller spec")
                print(f"  3. Manually copy to dist/ if stdlib module")
                return False
            else:
                print("✅ All required imports are present in bundle!")
                return True
                
        except Exception as e:
            print(f"ERROR during validation: {e}")
            import traceback
            traceback.print_exc()
            return False


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    tarball_path = sys.argv[1]
    success = validate_bundle(tarball_path)
    sys.exit(0 if success else 1)
