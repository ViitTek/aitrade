param(
    [string]$SourceImage = "c:\aiinvest\csharp-ui\AIInvestLauncher\assets\aiinvest_source.png",
    [string]$OutIco = "c:\aiinvest\csharp-ui\AIInvestLauncher\assets\aiinvest.ico"
)

if (-not (Test-Path $SourceImage)) {
    throw "Source image not found: $SourceImage"
}

$outDir = Split-Path -Parent $OutIco
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

$python = "c:\aiinvest\python-core\venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

$code = @'
from PIL import Image
import sys

src = sys.argv[1]
out = sys.argv[2]

img = Image.open(src).convert("RGBA")
sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
img.save(out, format="ICO", sizes=sizes)
print("ICON_OK " + out)
'@

 $tmpPy = [System.IO.Path]::Combine([System.IO.Path]::GetTempPath(), "aiinvest_make_icon.py")
 Set-Content -Path $tmpPy -Value $code -Encoding UTF8
 try {
    & $python $tmpPy $SourceImage $OutIco
    if ($LASTEXITCODE -ne 0) {
        throw "Icon generation failed"
    }
 }
 finally {
    if (Test-Path $tmpPy) { Remove-Item $tmpPy -Force -ErrorAction SilentlyContinue }
}
