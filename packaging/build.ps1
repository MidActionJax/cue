# Builds Cue.exe (PyInstaller) and the installer (Inno Setup).
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1 [-Out E:\CueDev] [-Python <python.exe>] [-SkipExe]
# The build is ~2 GB (mostly CUDA libraries), so point -Out at a drive with space.
# Needs: pip install -r requirements.txt pyinstaller, and Inno Setup 6 (winget install JRSoftware.InnoSetup).
param(
    [string]$Out = "$env:USERPROFILE\.cue\build",
    [string]$Python = "python",
    [switch]$SkipExe
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$release = (& $Python -c "import cue; print(cue.__release__)").Trim()
$version = (& $Python -c "import cue; print(cue.__version__)").Trim()
Write-Host "Cue $release -> $Out"

# stamp the version into the exe's Properties -> Details (version_info.txt follows cue/__init__.py)
$vi = Join-Path $PSScriptRoot "version_info.txt"
$parts = ($version.Split(".") + @("0", "0", "0"))[0..3] -join ", "
$text = [IO.File]::ReadAllText($vi)
$text = [regex]::Replace($text, "(filevers|prodvers)=\([^)]*\)", "`$1=($parts)")
$text = [regex]::Replace($text, "StringStruct\('FileVersion', '[^']*'\)", "StringStruct('FileVersion', '$version')")
$text = [regex]::Replace($text, "StringStruct\('ProductVersion', '[^']*'\)", "StringStruct('ProductVersion', '$($release.Replace('-', ' '))')")
[IO.File]::WriteAllText($vi, $text, (New-Object Text.UTF8Encoding $false))

if (-not $SkipExe) {
    & $Python -m PyInstaller packaging/cue.spec --noconfirm --workpath "$Out\build" --distpath "$Out\dist"
    if ($LASTEXITCODE) { throw "PyInstaller failed" }
}

$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
& $iscc "/DAppVersion=$release" "/DAppVersionNum=$version" "/DDistDir=$Out\dist\Cue" "/O$Out\installer" packaging\cue.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
Get-ChildItem "$Out\installer\Cue-Setup-$release.exe" | ForEach-Object { "{0}  {1:N0} MB" -f $_.FullName, ($_.Length / 1MB) }
