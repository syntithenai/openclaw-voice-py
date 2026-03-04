#!/usr/bin/env bash
set -euo pipefail

PRECISE_REPO="${PRECISE_REPO:-https://github.com/MycroftAI/mycroft-precise}"
PRECISE_REF="${PRECISE_REF:-v0.3.0}"
OUT_DIR="${OUT_DIR:-/out}"

mkdir -p "$OUT_DIR"

cd /opt
git clone "$PRECISE_REPO" mycroft-precise
cd mycroft-precise
git checkout "$PRECISE_REF"

# Install in a dedicated venv matching upstream packaging expectations
# Reuse system packages (scipy/h5py) installed by apt to reduce ARM wheel download issues.
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
# Keep pip cache for faster rebuilds (mounted from host)
# pip cache purge || true

# Use piwheels where available (important for ARM + legacy deps)
pip config set global.extra-index-url https://www.piwheels.org/simple || true

# Runner package (lightweight)
pip install -e runner/

# Core runtime deps needed to package engine.
# Use architecture-specific install strategy to reduce wheel mismatch issues.
TARGET_ARCH="${TARGET_ARCH:-armv7}"

download_wheel() {
  local url="$1"
  local out="$2"
  local tries=8
  local i=1
  while [ "$i" -le "$tries" ]; do
    rm -f "$out"
    if curl -L --fail "$url" -o "$out"; then
      return 0
    fi
    echo "download failed (attempt $i/$tries): $url"
    i=$((i+1))
    sleep 2
  done
  echo "ERROR: failed to download after $tries attempts: $url"
  return 1
}

if [ "$TARGET_ARCH" = "armv7" ]; then
  # Pin direct ARMv7 wheels for better reproducibility.
  TF_WHL_URL="https://archive1.piwheels.org/simple/tensorflow/tensorflow-1.13.1-cp37-none-linux_armv7l.whl"
  NP_WHL_URL="https://archive1.piwheels.org/simple/numpy/numpy-1.16.0-cp37-cp37m-linux_armv7l.whl"
  H5_WHL_URL="https://archive1.piwheels.org/simple/h5py/h5py-2.10.0-cp37-cp37m-linux_armv7l.whl"
  KR_WHL_URL="https://archive1.piwheels.org/simple/keras/Keras-2.1.5-py2.py3-none-any.whl"

  TF_WHL_FILE=/tmp/tensorflow-1.13.1-cp37-none-linux_armv7l.whl
  NP_WHL_FILE=/tmp/numpy-1.16.0-cp37-cp37m-linux_armv7l.whl
  H5_WHL_FILE=/tmp/h5py-2.10.0-cp37-cp37m-linux_armv7l.whl
  KR_WHL_FILE=/tmp/Keras-2.1.5-py2.py3-none-any.whl

  download_wheel "$TF_WHL_URL" "$TF_WHL_FILE"
  download_wheel "$NP_WHL_URL" "$NP_WHL_FILE"
  download_wheel "$H5_WHL_URL" "$H5_WHL_FILE"
  download_wheel "$KR_WHL_URL" "$KR_WHL_FILE"

  pip install --no-deps "$TF_WHL_FILE"
  pip install --no-deps "$NP_WHL_FILE" "$H5_WHL_FILE" "$KR_WHL_FILE"
else
  # ARM64: TensorFlow 1.13.1 has no official/default aarch64 wheel in modern indexes.
  # Provide a compatible wheel URL via TF113_AARCH64_WHL_URL.
  if [ -z "${TF113_AARCH64_WHL_URL:-}" ]; then
    echo "ERROR: ARM64 build requires TF113_AARCH64_WHL_URL (TensorFlow 1.13.1 aarch64 wheel URL)."
    echo "Example usage: TF113_AARCH64_WHL_URL=<url-to-tensorflow-1.13.1-aarch64.whl> ./build_precise_engine_arm64.sh"
    exit 2
  fi

  TF_WHL_FILE=/tmp/tensorflow-1.13.1-cp37-linux_aarch64.whl
  download_wheel "$TF113_AARCH64_WHL_URL" "$TF_WHL_FILE"
  pip install --no-deps "$TF_WHL_FILE"

  # Remaining runtime packages for arm64 from standard indexes.
  pip install --no-deps \
    numpy==1.16.0 \
    h5py==2.10.0 \
    keras==2.1.5
fi

# Install scipy AFTER numpy is pinned to avoid numpy version conflicts
# scipy 1.3.3 is compatible with numpy 1.16.0 and available on piwheels as pre-built wheel
echo "Installing scipy from piwheels (compatible with numpy 1.16.0)..."
pip install --no-deps scipy==1.3.3

# Remaining constrained deps
# Allow pip cache for faster rebuilds
pip install \
  protobuf==3.20.3 \
  absl-py==0.7.1 \
  wrapt==1.11.2 \
  gast \
  astor \
  termcolor \
  keras-applications \
  keras-preprocessing \
  pyinstaller==5.13.2

# Install lightweight extras without forcing a scipy wheel re-resolve.
# Allow pip cache for faster rebuilds
pip install --no-deps \
  sonopy \
  pyaudio \
  wavio \
  prettyparse==0.2.0 \
  attrs \
  fitipy==0.1.2 \
  speechpy-fast \
  pyache==0.1.0 \
  pyyaml \
  six \
  wrapt

# typing backport conflicts with PyInstaller on Python 3.7+
pip uninstall -y typing >/dev/null 2>&1 || true

pip install -e . --no-deps

# Build engine-only artifact without invoking ./build.sh (which calls setup.sh + sudo)
# Add system scipy to PyInstaller's search path explicitly
SCIPY_PATH="/usr/lib/python3/dist-packages"
tmp_spec="$(mktemp).spec"
sed -e 's/%%SCRIPT%%/engine/g' -e 's/%%TRAIN_LIBS%%/False/g' precise.template.spec \
  | sed "s/hidden_imports = \['prettyparse', 'speechpy'\]/hidden_imports = ['prettyparse', 'speechpy', 'scipy', 'scipy.signal', 'tensorflow', 'tensorflow_core', 'tensorflow_core.python', 'tensorflow_core.keras', 'tensorflow_core.ops', 'google', 'google.protobuf', 'absl', 'wrapt', 'gast', 'termcolor', 'keras_applications', 'keras_preprocessing', 'xml', 'xml.dom', 'xml.dom.minidom', 'timeit', 'fractions']/" \
  | sed "s|^a = Analysis(|a = Analysis(|" \
  > "$tmp_spec"
pyinstaller -y "$tmp_spec"

# PyInstaller's hidden_imports doesn't work reliably for stdlib modules.
# Manually inject critical stdlib modules into base_library.zip
echo "Injecting stdlib modules into base_library.zip..."
TEMP_ZIP_DIR="$(mktemp -d)"
cd "$TEMP_ZIP_DIR"
unzip -q /opt/mycroft-precise/dist/precise-engine/base_library.zip

# Add stdlib modules that absl/tensorflow need
for mod in timeit logging traceback linecache tokenize token inspect ast dis opcode textwrap string fractions; do
  MOD_FILE="$(python -c "import ${mod}; print(${mod}.__file__)" 2>/dev/null | grep '\.py$' || true)"
  if [ -n "$MOD_FILE" ] && [ -f "$MOD_FILE" ] && [[ "$MOD_FILE" == *.py ]]; then
    # Only copy if not already present
    if [ ! -f "$(basename "$MOD_FILE")" ]; then
      cp "$MOD_FILE" .
      echo "  Added $(basename "$MOD_FILE") to base_library.zip"
    fi
  fi
done

# Add xml package (recursively, including submodules xml.dom, xml.etree, etc.)
XML_DIR="$(python -c "import xml, os; print(os.path.dirname(xml.__file__))")"
if [ -d "$XML_DIR" ] && [ ! -d "./xml" ]; then
  cp -r "$XML_DIR" ./xml  
  echo "  Added xml/ package (with submodules) to base_library.zip"
fi

# Explicitly ensure xml.dom submodule is present
XML_DOM_DIR="$(python -c "import xml.dom, os; print(os.path.dirname(xml.dom.__file__))" 2>/dev/null || echo "")"
if [ -n "$XML_DOM_DIR" ] && [ -d "$XML_DOM_DIR" ]; then
  mkdir -p ./xml/dom
  cp -r "$XML_DOM_DIR"/* ./xml/dom/ 2>/dev/null || true
  echo "  Ensured xml/dom/ submodule in base_library.zip"
fi
# Add gast (needed by TensorFlow autograph)
GAST_DIR="$(python -c "import site; sp = site.getsitepackages()[0]; print(sp + '/gast')" 2>/dev/null || echo "")"
if [ -n "$GAST_DIR" ] && [ -d "$GAST_DIR" ] && [ ! -d "./gast" ]; then
  cp -r "$GAST_DIR" ./gast
  echo "  Added gast/ package to base_library.zip"
else
  echo "  WARNING: Could not add gast to base_library.zip"
fi

# Add termcolor (needed by TensorFlow autograph)
TERMCOLOR_DIR="$(python -c "import site; sp = site.getsitepackages()[0]; print(sp + '/termcolor')" 2>/dev/null || echo "")"
if [ -n "$TERMCOLOR_DIR" ] && [ -d "$TERMCOLOR_DIR" ] && [ ! -d "./termcolor" ]; then
  cp -r "$TERMCOLOR_DIR" ./termcolor
  echo "  Added termcolor/ package to base_library.zip"
else
  echo "  WARNING: Could not add termcolor to base_library.zip"
fi

# Repack the zip
rm /opt/mycroft-precise/dist/precise-engine/base_library.zip
zip -qr /opt/mycroft-precise/dist/precise-engine/base_library.zip .
cd /opt/mycroft-precise
rm -rf "$TEMP_ZIP_DIR"

echo "Stdlib modules injected into base_library.zip"

# PyInstaller failed to bundle scipy even with hidden imports - manually copy it
echo "Manually adding scipy to bundle (PyInstaller failed to auto-detect it)..."
SCIPY_SRC_DIR="$(python -c 'import scipy, os; print(os.path.dirname(scipy.__file__))')"
if [ -d "$SCIPY_SRC_DIR" ]; then
  cp -r "$SCIPY_SRC_DIR" dist/precise-engine/scipy
  echo "Copied scipy from $SCIPY_SRC_DIR to dist/precise-engine/scipy"
else
  echo "WARNING: scipy source dir not found at $SCIPY_SRC_DIR"
fi

# TensorFlow has nested module structure that PyInstaller misses - manually copy both tensorflow and tensorflow_core

echo "Manually adding TensorFlow core modules to bundle (PyInstaller failed to bundle tensorflow_core.python)..."
TF_SRC_DIR="$(python -c 'import site; sp = site.getsitepackages()[0]; print(sp + "/tensorflow")')"
if [ -d "$TF_SRC_DIR" ]; then
  cp -r "$TF_SRC_DIR" dist/precise-engine/tensorflow
  echo "Copied tensorflow from $TF_SRC_DIR to dist/precise-engine/tensorflow"
else
  echo "WARNING: tensorflow source dir not found"
fi

# Also manually copy tensorflow_core if it's in site-packages
TF_CORE_DIR="$(python -c 'import site; sp = site.getsitepackages()[0]; print(sp + "/tensorflow_core")')"
if [ -d "$TF_CORE_DIR" ]; then
  cp -r "$TF_CORE_DIR" dist/precise-engine/tensorflow_core
  echo "Copied tensorflow_core from $TF_CORE_DIR to dist/precise-engine/tensorflow_core"
else
  echo "WARNING: tensorflow_core source dir not found"
fi

# google.protobuf is also needed by TensorFlow
echo "Manually adding google.protobuf to bundle..."
GOOGLE_DIR="$(python -c 'import site; sp = site.getsitepackages()[0]; print(sp + "/google")')"
if [ -d "$GOOGLE_DIR" ]; then
  cp -r "$GOOGLE_DIR" dist/precise-engine/google
  echo "Copied google from $GOOGLE_DIR to dist/precise-engine/google"
else
  echo "WARNING: google module dir not found"
fi

# Manually add xml.dom submodule (PyInstaller misses it even with hidden_imports)
echo "Manually adding xml.dom submodule to bundle..."
XML_DOM_DIR="$(python -c 'import xml.dom, os; print(os.path.dirname(xml.dom.__file__))')"
if [ -d "$XML_DOM_DIR" ]; then
  mkdir -p dist/precise-engine/xml/dom
  cp -r "$XML_DOM_DIR"/* dist/precise-engine/xml/dom/
  echo "Copied xml.dom from $XML_DOM_DIR to dist/precise-engine/xml/dom"
else
  echo "WARNING: xml.dom source dir not found"
fi

# absl-py is a TensorFlow dependency
echo "Manually adding absl module to bundle..."
ABSL_DIR=""
# Check if absl is directly importable
ABSL_DIR="$(python -c 'import site; sp = site.getsitepackages()[0]; import os; absl_path = sp + "/absl"; print(absl_path if os.path.exists(absl_path) else "")' 2>/dev/null)"

if [ -z "$ABSL_DIR" ] || [ ! -d "$ABSL_DIR" ]; then
  # If not found, try to search in site-packages
  ABSL_DIR="$(python -c 'import site, os; sp = site.getsitepackages()[0]; matches = [f for f in os.listdir(sp) if "absl" in f.lower()]; print(os.path.join(sp, matches[0]) if matches else "")' 2>/dev/null)"
fi

if [ -n "$ABSL_DIR" ] && [ -d "$ABSL_DIR" ]; then
  cp -r "$ABSL_DIR" dist/precise-engine/absl
  echo "Copied absl from $ABSL_DIR to dist/precise-engine/absl"
else
  echo "WARNING: absl module dir not found; TensorFlow may fail at runtime"
fi

# Manually add wrapt (TensorFlow dependency for decorators)
echo "Manually adding wrapt module to bundle..."
WRAPT_DIR="$(python -c 'import site; sp = site.getsitepackages()[0]; import os; wrapt_path = sp + "/wrapt"; print(wrapt_path if os.path.exists(wrapt_path) else "")' 2>/dev/null)"
if [ -n "$WRAPT_DIR" ] && [ -d "$WRAPT_DIR" ]; then
  cp -r "$WRAPT_DIR" dist/precise-engine/wrapt
  echo "Copied wrapt from $WRAPT_DIR to dist/precise-engine/wrapt"
else
  echo "WARNING: wrapt module dir not found; TensorFlow may fail at runtime"
fi

# Manually add astor (TensorFlow autograph compiler dependency)
echo "Manually adding astor module to bundle..."
ASTOR_DIR="$(python -c 'import site; sp = site.getsitepackages()[0]; import os; astor_path = sp + "/astor"; print(astor_path if os.path.exists(astor_path) else "")' 2>/dev/null)"
if [ -n "$ASTOR_DIR" ] && [ -d "$ASTOR_DIR" ]; then
  cp -r "$ASTOR_DIR" dist/precise-engine/astor
  echo "Copied astor from $ASTOR_DIR to dist/precise-engine/astor"
else
  echo "WARNING: astor module dir not found; TensorFlow may fail at runtime"
fi

# Manually add keras (TensorFlow backend)
echo "Manually adding keras module to bundle..."
KERAS_DIR="$(python -c 'import site; sp = site.getsitepackages()[0]; import os; keras_path = sp + "/keras"; print(keras_path if os.path.exists(keras_path) else "")' 2>/dev/null)"
if [ -n "$KERAS_DIR" ] && [ -d "$KERAS_DIR" ]; then
  cp -r "$KERAS_DIR" dist/precise-engine/keras
  echo "Copied keras from $KERAS_DIR to dist/precise-engine/keras"
else
  echo "WARNING: keras module dir not found"
fi

# Manually add keras_applications (TensorFlow keras dependency)
echo "Manually adding keras_applications module to bundle..."
# keras-applications package installs with capital K and underscore
KERAS_APPS_DIR=""
KERAS_APPS_DIR="$(python -c 'import site; sp = site.getsitepackages()[0]; import os; ka_path = sp + "/keras_applications"; print(ka_path if os.path.exists(ka_path) else "")' 2>/dev/null)"
if [ -z "$KERAS_APPS_DIR" ]; then
  # Try with capital K
  KERAS_APPS_DIR="$(python -c 'import site; sp = site.getsitepackages()[0]; import os; ka_path = sp + "/Keras_Applications"; print(ka_path if os.path.exists(ka_path) else "")' 2>/dev/null)"
fi
if [ -n "$KERAS_APPS_DIR" ] && [ -d "$KERAS_APPS_DIR" ]; then
  cp -r "$KERAS_APPS_DIR" dist/precise-engine/keras_applications
  echo "Copied keras_applications from $KERAS_APPS_DIR to dist/precise-engine/keras_applications"
else
  echo "WARNING: keras_applications module dir not found (tried keras_applications and Keras_Applications)"
fi

# Manually add keras_preprocessing (TensorFlow keras dependency)
echo "Manually adding keras_preprocessing module to bundle..."
# keras-preprocessing package installs with capital K and underscore
KERAS_PREP_DIR=""
KERAS_PREP_DIR="$(python -c 'import site; sp = site.getsitepackages()[0]; import os; kp_path = sp + "/keras_preprocessing"; print(kp_path if os.path.exists(kp_path) else "")' 2>/dev/null)"
if [ -z "$KERAS_PREP_DIR" ]; then
  # Try with capital K
  KERAS_PREP_DIR="$(python -c 'import site; sp = site.getsitepackages()[0]; import os; kp_path = sp + "/Keras_Preprocessing"; print(kp_path if os.path.exists(kp_path) else "")' 2>/dev/null)"
fi
if [ -n "$KERAS_PREP_DIR" ] && [ -d "$KERAS_PREP_DIR" ]; then
  cp -r "$KERAS_PREP_DIR" dist/precise-engine/keras_preprocessing
  echo "Copied keras_preprocessing from $KERAS_PREP_DIR to dist/precise-engine/keras_preprocessing"
else
  echo "WARNING: keras_preprocessing module dir not found (tried keras_preprocessing and Keras_Preprocessing)"
fi

# Manually add attrs (Precise dependency) - try both 'attr' and 'attrs' dirs
echo "Manually adding attrs module to bundle..."
ATTRS_DIR_ATTR="$(python -c 'import site; sp = site.getsitepackages()[0]; import os; attrs_path = sp + "/attr"; print(attrs_path if os.path.exists(attrs_path) else "")' 2>/dev/null)"
ATTRS_DIR_ATTRS="$(python -c 'import site; sp = site.getsitepackages()[0]; import os; attrs_path = sp + "/attrs"; print(attrs_path if os.path.exists(attrs_path) else "")' 2>/dev/null)"

if [ -n "$ATTRS_DIR_ATTR" ] && [ -d "$ATTRS_DIR_ATTR" ]; then
  cp -r "$ATTRS_DIR_ATTR" dist/precise-engine/attr
  # Also create attrs symlink for import compatibility
  ln -s attr dist/precise-engine/attrs 2>/dev/null || true
  echo "Copied attrs from $ATTRS_DIR_ATTR to dist/precise-engine/attr"
elif [ -n "$ATTRS_DIR_ATTRS" ] && [ -d "$ATTRS_DIR_ATTRS" ]; then
  cp -r "$ATTRS_DIR_ATTRS" dist/precise-engine/attrs
  echo "Copied attrs from $ATTRS_DIR_ATTRS to dist/precise-engine/attrs"
else
  echo "WARNING: attrs module dir not found (tried both 'attr' and 'attrs')"
fi

# Manually add prettyparse (Precise dependency)
echo "Manually adding prettyparse module to bundle..."
PRETTYPARSE_DIR="$(python -c 'import site; sp = site.getsitepackages()[0]; import os; pp_path = sp + "/prettyparse"; print(pp_path if os.path.exists(pp_path) else "")' 2>/dev/null)"
if [ -n "$PRETTYPARSE_DIR" ] && [ -d "$PRETTYPARSE_DIR" ]; then
  cp -r "$PRETTYPARSE_DIR" dist/precise-engine/prettyparse
  echo "Copied prettyparse from $PRETTYPARSE_DIR to dist/precise-engine/prettyparse"
else
  echo "WARNING: prettyparse module dir not found"
fi

# Manually add speechpy (Precise dependency)
echo "Manually adding speechpy module to bundle..."
SPEECHPY_DIR="$(python -c 'import site; sp = site.getsitepackages()[0]; import os; sp_path = sp + "/speechpy"; print(sp_path if os.path.exists(sp_path) else "")' 2>/dev/null)"
if [ -n "$SPEECHPY_DIR" ] && [ -d "$SPEECHPY_DIR" ]; then
  cp -r "$SPEECHPY_DIR" dist/precise-engine/speechpy
  echo "Copied speechpy from $SPEECHPY_DIR to dist/precise-engine/speechpy"
else
  echo "WARNING: speechpy module dir not found"
fi

# Proactively bundle stdlib modules - REMOVED, now injecting into base_library.zip instead

echo "Validating base_library.zip contents..."
python - <<'PY'
import zipfile
import sys

zip_path = "dist/precise-engine/base_library.zip"
required_modules = ["timeit.py", "xml/__init__.py", "xml/dom/minidom.py"]

try:
    with zipfile.ZipFile(zip_path, 'r') as zf:
        files = zf.namelist()
        missing = [m for m in required_modules if m not in files]
        
        if missing:
            print(f"Bundle validation failed - missing from base_library.zip: {missing}")
            sys.exit(1)
        else:
            print(f"Bundle validation passed - all {len(required_modules)} critical stdlib modules present in base_library.zip")
except Exception as e:
    print(f"Bundle validation error: {e}")
    sys.exit(1)
PY

PRECISE_VERSION="$(python - << 'PY'
from precise import __version__
print(__version__)
PY
)"
OUT_NAME="precise-engine_${PRECISE_VERSION}_$(uname -m).tar.gz"
mkdir -p dist
(cd dist && tar czvf "$OUT_NAME" precise-engine && md5sum "$OUT_NAME" > "$OUT_NAME.md5")

# Copy out best matching engine tarball
ENGINE_TAR="$(ls -1 dist/precise-engine_*_"${ARCH_GLOB}".tar.gz | head -n1 || true)"
if [ -z "$ENGINE_TAR" ]; then
  echo "ERROR: did not find dist/precise-engine_*_${ARCH_GLOB}.tar.gz"
  ls -la dist || true
  exit 1
fi

cp "$ENGINE_TAR" "$OUT_DIR/precise-engine.tar.gz"
cp "${ENGINE_TAR}.md5" "$OUT_DIR/precise-engine.tar.gz.md5" || true

echo "Build complete: $OUT_DIR/precise-engine.tar.gz"
