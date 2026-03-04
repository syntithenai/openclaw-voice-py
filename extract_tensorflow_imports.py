#!/usr/bin/env python3
"""
Extract all import statements from TensorFlow to ensure comprehensive bundling.
This helps identify missing dependencies before they cause runtime failures.

Usage:
    # Inside Docker build container or a Python 3.7 environment with TensorFlow 1.13.1:
    python3 extract_tensorflow_imports.py
"""

import ast
import os
import sys
from pathlib import Path
from collections import defaultdict


def extract_imports_from_file(filepath):
    """Extract all import statements from a Python file."""
    imports = set()
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            tree = ast.parse(f.read(), filename=str(filepath))
            
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split('.')[0])
    except Exception as e:
        # Ignore parse errors
        pass
    
    return imports


def scan_tensorflow_imports(tensorflow_path):
    """Scan all Python files in TensorFlow to find import dependencies."""
    print(f"Scanning TensorFlow at: {tensorflow_path}\n")
    
    all_imports = defaultdict(set)
    
    for py_file in Path(tensorflow_path).rglob('*.py'):
        imports = extract_imports_from_file(py_file)
        for imp in imports:
            all_imports[imp].add(str(py_file.relative_to(tensorflow_path)))
    
    return all_imports


def filter_imports(imports):
    """Filter to show only external dependencies (not tensorflow itself)."""
    # Ignore tensorflow's own modules and very common stdlib
    ignore = {
        'tensorflow', 'tensorflow_core', 'tensorflow_estimator',
        'os', 'sys', 're', 'json', 'logging', 'collections', 'functools',
        'threading', 'multiprocessing', 'subprocess', 'tempfile', 'shutil',
        'io', 'contextlib', 'itertools', 'warnings', 'weakref', 'copy',
        'pickle', 'struct', 'hashlib', 'binascii', 'base64', 'uuid',
        'datetime', 'time', 'math', 'random', 'typing', 'types',
    }
    
    return {k: v for k, v in imports.items() if k not in ignore}


if __name__ == '__main__':
    # Try to find TensorFlow installation
    try:
        import tensorflow as tf
        tf_path = Path(tf.__file__).parent
    except ImportError:
        print("ERROR: TensorFlow not installed")
        print("Run this script inside the Docker build container or a TF 1.13.1 environment")
        sys.exit(1)
    
    # Scan tensorflow and tensorflow_core
    all_imports = scan_tensorflow_imports(tf_path)
    
    # Also scan tensorflow_core if it exists separately
    tf_core_path = tf_path.parent / 'tensorflow_core'
    if tf_core_path.exists():
        core_imports = scan_tensorflow_imports(tf_core_path)
        for module, files in core_imports.items():
            all_imports[module].update(files)
    
    # Filter to external deps
    external = filter_imports(all_imports)
    
    print("="*70)
    print("External dependencies imported by TensorFlow:")
    print("="*70)
    print()
    
    for module in sorted(external.keys()):
        file_count = len(external[module])
        print(f"  {module:25s} (used in {file_count:3d} files)")
    
    print()
    print("="*70)
    print("Add these to PyInstaller hidden_imports:")
    print("="*70)
    hidden_imports = sorted(external.keys())
    print(f"hidden_imports = {hidden_imports}")
    print()
    print("="*70)
    print("Ensure these are pip-installed:")
    print("="*70)
    # Common external packages TensorFlow 1.13.1 needs
    known_packages = ['numpy', 'scipy', 'h5py', 'keras', 'wrapt', 'protobuf', 
                      'absl-py', 'gast', 'astor', 'termcolor', 'six']
    for pkg in known_packages:
        if any(pkg in imp for imp in external.keys()):
            print(f"  pip install {pkg}")
