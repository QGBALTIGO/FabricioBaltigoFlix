#!/bin/sh
set -eu

# RapidOCR depends on the desktop OpenCV wheel by package metadata. In a
# headless container that wheel pulls GUI/X11 libraries (for example libxcb)
# that are not present in Railway's slim runtime. Replace it after dependency
# installation with the server-safe wheel that exposes the same cv2 module.
python -m pip uninstall -y opencv-python opencv-contrib-python >/dev/null 2>&1 || true
python -m pip install --no-cache-dir --force-reinstall opencv-python-headless==5.0.0.93

python - <<'PY'
import cv2
print(f"OpenCV headless ready: {cv2.__version__}")
PY
