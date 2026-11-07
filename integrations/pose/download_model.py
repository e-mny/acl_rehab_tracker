"""Pin model contents as well as its versioned upstream URL."""
import hashlib
import sys
import urllib.request
from pathlib import Path

URL = 'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task'
SHA256 = '59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a'


def download(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = urllib.request.urlopen(URL, timeout=120).read()
    if hashlib.sha256(content).hexdigest() != SHA256:
        raise ValueError('Pose model checksum did not match the pinned version.')
    path.write_bytes(content)


if __name__ == '__main__':
    download(sys.argv[1])
