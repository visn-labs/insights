"""Bounded runtime identity probe; metadata only, never loads a detector/model."""
import hashlib
from importlib import metadata
import json
from pathlib import Path
import sys


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            result.update(chunk)
    return result.hexdigest()


mode = sys.argv[1]
packages = ['numpy', 'opencv-python', 'opencv-python-headless']
required = ['av', 'onnxruntime', 'imageio-ffmpeg'] if mode == 'privacy' else ['torch', 'ultralytics']
versions = {}
for name in packages + required:
    try: versions[name] = metadata.version(name)
    except metadata.PackageNotFoundError:
        if name in required: raise
if 'numpy' not in versions or not any(name in versions for name in ['opencv-python', 'opencv-python-headless']):
    raise RuntimeError('required_runtime_distribution_missing')
result = dict(schema='visn-execution-runtime-v1', python=sys.version,
              executable_sha256=digest(sys.executable), packages=versions)
if mode == 'privacy':
    import imageio_ffmpeg
    result['ffmpeg_sha256'] = digest(imageio_ffmpeg.get_ffmpeg_exe())
print(json.dumps(result, sort_keys=True, allow_nan=False))
