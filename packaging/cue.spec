# PyInstaller spec for Cue.  Build with packaging/build.ps1 (or: pyinstaller packaging/cue.spec).
# Produces dist/Cue/ with two executables sharing one set of libraries:
#   Cue.exe      the app (no console window)
#   cue-cli.exe  same commands with console output: cue-cli worklog | note "..." | prep | devices
import glob
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

REPO = os.path.abspath(os.path.join(SPECPATH, ".."))
SITE = next(p for p in sys.path if p.lower().endswith("site-packages"))
ICON = os.path.join(REPO, "assets", "cue.ico")
VERSION = os.path.join(SPECPATH, "version_info.txt")

# CUDA for Whisper on the GPU. cuDNN's RNN/attention library and NVRTC aren't used by
# CTranslate2's Whisper path (tested), which saves ~436 MB.
SKIP_DLLS = {"cudnn_adv64_9.dll"}
binaries = []
for pkg in ("cublas", "cudnn"):
    for dll in glob.glob(os.path.join(SITE, "nvidia", pkg, "bin", "*.dll")):
        if os.path.basename(dll) not in SKIP_DLLS:
            binaries.append((dll, f"nvidia/{pkg}/bin"))
for mod in ("ctranslate2", "sherpa_onnx", "onnxruntime"):
    binaries += collect_dynamic_libs(mod)

datas = [
    (os.path.join(REPO, "assets", "*.ico"), "assets"),
    (os.path.join(REPO, "assets", "*.png"), "assets"),
    (os.path.join(REPO, "config.example.yaml"), "."),
    (os.path.join(REPO, "profiles", "me.example.md"), "profiles"),
    (os.path.join(REPO, "profiles", "work", "*.example.md"), os.path.join("profiles", "work")),
    (os.path.join(REPO, "profiles", "interview", "README.md"), os.path.join("profiles", "interview")),
    (os.path.join(REPO, "profiles", "sbir", "*.example.md"), os.path.join("profiles", "sbir")),
    (os.path.join(REPO, "profiles", "sbir", "README.md"), os.path.join("profiles", "sbir")),
    (os.path.join(REPO, "profiles", "docs", "README.md"), os.path.join("profiles", "docs")),
    (os.path.join(REPO, "models", "wespeaker_en_voxceleb_resnet34_LM.onnx"), "models"),
]
datas += collect_data_files("faster_whisper")

hiddenimports = (collect_submodules("cue") + collect_submodules("worklog")
                 + ["pycaw.pycaw", "comtypes.client", "webrtcvad", "keyboard", "soxr", "pyaudiowpatch",
                    "anthropic", "httpx", "pypdf", "yaml", "PyQt6.QtNetwork", "openpyxl"])

a = Analysis(
    [os.path.join(SPECPATH, "cue_entry.py")],
    pathex=[REPO],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[os.path.join(SPECPATH, "hooks")],
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "torch", "tensorflow", "pandas", "scipy",
              "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtQml", "PyQt6.QtQuick",
              "PyQt6.QtMultimedia", "PyQt6.QtPdf", "PyQt6.QtOpenGL", "PyQt6.QtSql"],
    noarchive=False,
)
pyz = PYZ(a.pure)

common = dict(exclude_binaries=True, icon=ICON, version=VERSION, upx=False, bootloader_ignore_signals=False)
gui = EXE(pyz, a.scripts, [], name="Cue", console=False, **common)
cli = EXE(pyz, a.scripts, [], name="cue-cli", console=True, **common)
coll = COLLECT(gui, cli, a.binaries, a.datas, name="Cue", upx=False)
