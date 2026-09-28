"""Make pip-installed CUDA libraries (nvidia-cublas-cu12, nvidia-cudnn-cu12) loadable on Windows.

faster-whisper / CTranslate2 need cuBLAS + cuDNN 9 DLLs. Installing them via pip avoids a
system-wide CUDA toolkit install, but Windows won't find them unless we add their bin dirs.
In the compiled app they're bundled under <app>/_internal/nvidia/*/bin.
"""
import glob
import os
import site
import sys


def add_cuda_dlls() -> list[str]:
    if os.name != "nt":
        return []
    roots = [getattr(sys, "_MEIPASS", "")]
    try:
        roots += list(site.getsitepackages()) + [site.getusersitepackages()]
    except AttributeError:  # frozen apps may not have a full site module
        pass
    roots.append(sys.prefix)
    added = []
    for root in filter(None, roots):
        for d in glob.glob(os.path.join(root, "**", "nvidia", "*", "bin"), recursive=True):
            if d in added:
                continue
            os.add_dll_directory(d)
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            added.append(d)
    return added
