# The stock hook copies metadata for "webrtcvad", but Cue uses the prebuilt "webrtcvad-wheels"
# distribution (same module); webrtcvad.py reads its version from that name at import time.
from PyInstaller.utils.hooks import copy_metadata

datas = copy_metadata("webrtcvad-wheels")
