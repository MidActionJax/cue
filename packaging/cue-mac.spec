# PyInstaller spec for Cue on macOS (Apple Silicon). Build with packaging/build-mac.sh.
# Produces dist/Cue.app. The one executable inside (Contents/MacOS/Cue) is both the app and the
# command line:  /Applications/Cue.app/Contents/MacOS/Cue worklog | note "..." | prep | devices
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

REPO = os.path.abspath(os.path.join(SPECPATH, ".."))
ICNS = os.environ.get("CUE_ICNS") or os.path.join(SPECPATH, "build", "Cue.icns")   # made by build-mac.sh
sys.path.insert(0, REPO)
import cue  # noqa: E402

binaries = []
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
    (os.path.join(REPO, "tools", "vscode-cue-trainer", "*"), os.path.join("tools", "vscode-cue-trainer")),
    (os.path.join(REPO, "evals", "trainer", "*.txt"), os.path.join("evals", "trainer")),
]
datas += collect_data_files("faster_whisper")
datas += collect_data_files("mlx_whisper")      # mel filters, tokenizer vocabularies
datas += collect_data_files("mlx")              # the Metal kernels (mlx.metallib)
binaries += collect_dynamic_libs("mlx")

hiddenimports = (collect_submodules("cue") + collect_submodules("worklog")
                 + collect_submodules("mlx_whisper") + collect_submodules("mlx")
                 + ["webrtcvad", "soxr", "sounddevice", "anthropic", "httpx", "pypdf", "yaml",
                    "PyQt6.QtNetwork", "openpyxl", "objc", "Foundation", "AppKit", "Quartz",
                    "ApplicationServices", "CoreMedia", "ScreenCaptureKit"]
                 + collect_submodules("pygments"))

a = Analysis(
    [os.path.join(SPECPATH, "cue_entry.py")],
    pathex=[REPO],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[os.path.join(SPECPATH, "hooks")],
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "torch", "tensorflow", "pandas", "scipy",
              "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtQml", "PyQt6.QtQuick",
              "PyQt6.QtMultimedia", "PyQt6.QtPdf", "PyQt6.QtOpenGL", "PyQt6.QtSql",
              "pyaudiowpatch", "keyboard", "pycaw", "comtypes"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Cue", console=False, upx=False,
          argv_emulation=False, target_arch="arm64", codesign_identity=None)
coll = COLLECT(exe, a.binaries, a.datas, name="Cue", upx=False)
app = BUNDLE(
    coll,
    name="Cue.app",
    icon=ICNS if os.path.exists(ICNS) else None,
    bundle_identifier="com.midactionjax.cue",
    version=cue.__version__,
    info_plist={
        "CFBundleName": "Cue",
        "CFBundleDisplayName": "Cue",
        "CFBundleShortVersionString": cue.__version__,
        "CFBundleVersion": cue.__version__,
        "LSMinimumSystemVersion": "13.0",           # ScreenCaptureKit system audio
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": "Cue listens to your side of the call to transcribe it and keep its answers in step.",
        "NSScreenCaptureUsageDescription": "Cue captures the call's audio (not your screen) to transcribe what the other people say.",
        "NSAppleEventsUsageDescription": "Cue opens your notes and files in Finder.",
        "NSHumanReadableCopyright": "Cue",
    },
)
