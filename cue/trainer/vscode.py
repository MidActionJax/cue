"""The Cue Trainer VS Code extension (tools/vscode-cue-trainer): packaging and installing it,
and opening your solution file in VS Code."""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import zipfile
from pathlib import Path

from ..config import APP_DIR, DATA

log = logging.getLogger(__name__)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
EXT_DIR = APP_DIR / "tools" / "vscode-cue-trainer"
EXT_ID = "midactionjax.cue-trainer"


def code_cli() -> str | None:
    return shutil.which("code") or shutil.which("code-insiders")


def _code(*args: str, timeout: float = 60) -> subprocess.CompletedProcess:
    exe = code_cli()
    if exe is None:
        raise FileNotFoundError("VS Code's `code` command isn't on PATH")
    return subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW)


def installed() -> bool:
    try:
        return EXT_ID in _code("--list-extensions", timeout=20).stdout.lower()
    except Exception:
        return False


def build_vsix(out: Path | None = None) -> Path:
    """A .vsix is a zip: [Content_Types].xml, extension.vsixmanifest and the extension under extension/."""
    pkg = json.loads((EXT_DIR / "package.json").read_text(encoding="utf-8"))
    out = out or DATA / "tmp" / f"cue-trainer-{pkg['version']}.vsix"
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest = f"""<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata>
    <Identity Language="en-US" Id="{pkg['name']}" Version="{pkg['version']}" Publisher="{pkg['publisher']}" />
    <DisplayName>{pkg['displayName']}</DisplayName>
    <Description xml:space="preserve">{pkg['description']}</Description>
    <Categories>Other</Categories>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="{pkg['engines']['vscode']}" />
    </Properties>
  </Metadata>
  <Installation><InstallationTarget Id="Microsoft.VisualStudio.Code" /></Installation>
  <Dependencies />
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true" />
  </Assets>
</PackageManifest>"""
    types = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension=".json" ContentType="application/json" />
  <Default Extension=".js" ContentType="application/javascript" />
  <Default Extension=".md" ContentType="text/markdown" />
  <Default Extension=".vsixmanifest" ContentType="text/xml" />
</Types>"""
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("extension.vsixmanifest", manifest)
        for f in EXT_DIR.iterdir():
            if f.is_file() and f.suffix in (".json", ".js", ".md"):
                z.write(f, f"extension/{f.name}")
    return out


def install() -> tuple[bool, str]:
    try:
        vsix = build_vsix()
        r = _code("--install-extension", str(vsix), "--force", timeout=120)
        ok = r.returncode == 0
        msg = (r.stdout + r.stderr).strip()[-300:]
        log.info("VS Code extension install rc=%s: %s", r.returncode, msg)
        return ok, msg
    except Exception as e:
        return False, str(e)


def open_file(path: Path, editor: str = "code") -> None:
    """Open your solution in the editor (VS Code reuses its window with -r)."""
    try:
        if editor == "code" and code_cli():
            subprocess.Popen([code_cli(), "-r", str(path)], creationflags=_NO_WINDOW)
        elif editor:
            exe = shutil.which(editor) or editor
            subprocess.Popen([exe, str(path)], creationflags=_NO_WINDOW)
    except OSError as e:
        log.warning("couldn't open %s in %s: %s", path, editor, e)
