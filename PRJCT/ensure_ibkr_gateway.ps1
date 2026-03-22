param(
    [string]$ProjectRoot = "C:\aiinvest",
    [int]$LoginTimeoutSec = 150,
    [switch]$SkipCredentialEntry,
    [switch]$SwitchOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;

public static class NativeFocus
{
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);

    [DllImport("user32.dll")]
    public static extern bool SetCursorPos(int x, int y);

    [DllImport("user32.dll")]
    public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
}
"@

function Write-Status {
    param([string]$Message)
    Write-Host "[IBKR-GATEWAY] $Message"
}

function Get-IntValue {
    param($Value, [int]$Default)
    try {
        if ($null -eq $Value) { return $Default }
        $text = [string]$Value
        if ([string]::IsNullOrWhiteSpace($text)) { return $Default }
        return [int]$text
    } catch {
        return $Default
    }
}

function Read-DotEnv {
    param([string]$Path)
    $values = @{}
    if (-not (Test-Path $Path)) { return $values }
    foreach ($line in Get-Content $Path) {
        $trimmed = if ($null -eq $line) { "" } else { [string]$line }
        $trimmed = $trimmed.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) { continue }
        $parts = $trimmed.Split("=", 2)
        if ($parts.Count -ne 2) { continue }
        $key = $parts[0].Trim()
        $value = $parts[1].Trim().Trim("'").Trim('"')
        if (-not [string]::IsNullOrWhiteSpace($key)) {
            $values[$key] = $value
        }
    }
    return $values
}

function Get-FirstEnvValue {
    param([hashtable]$Values, [string[]]$Names)
    foreach ($name in $Names) {
        if ($Values.ContainsKey($name)) {
            $value = [string]$Values[$name]
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                return $value.Trim()
            }
        }
    }
    return ""
}

function Normalize-TradingMode {
    param([string]$Mode)
    $raw = if ($null -eq $Mode) { "" } else { [string]$Mode }
    $raw = $raw.Trim().ToLowerInvariant()
    if ($raw -in @("live", "l")) { return "live" }
    return "paper"
}

function Get-PortCandidates {
    param([int]$ConfiguredPort, [string]$TradingMode)
    $ports = New-Object System.Collections.Generic.List[int]
    $preferred = if ($TradingMode -eq "live") { @(4001, 7496) } else { @(4002, 7497) }
    $fallback = if ($TradingMode -eq "live") { @(4002, 7497) } else { @(4001, 7496) }
    foreach ($port in @($ConfiguredPort) + $preferred + $fallback) {
        if ($port -le 0 -or $ports.Contains($port)) { continue }
        $ports.Add($port)
    }
    return $ports
}

function Test-TcpPort {
    param([string]$HostName, [int]$Port, [int]$TimeoutMs = 1200)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect($HostName, $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne($TimeoutMs)
        if (-not $ok) { return $false }
        $client.EndConnect($iar) | Out-Null
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-OpenPort {
    param([string]$HostName, [System.Collections.Generic.List[int]]$Ports)
    foreach ($port in $Ports) {
        if (Test-TcpPort -HostName $HostName -Port $port) {
            return $port
        }
    }
    return $null
}

function Get-GatewayProcesses {
    return @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ProcessName -match "^(ibgateway|tws)$"
    } | Sort-Object StartTime -Descending)
}

function Find-GatewayExecutable {
    $running = Get-GatewayProcesses
    foreach ($proc in $running) {
        try {
            if (-not [string]::IsNullOrWhiteSpace($proc.Path) -and (Test-Path $proc.Path)) {
                return $proc.Path
            }
        } catch {
        }
    }

    $roots = @("C:\Jts\ibgateway", "C:\Jts")
    foreach ($root in $roots) {
        if (-not (Test-Path $root)) { continue }
        $match = Get-ChildItem $root -Filter "ibgateway.exe" -Recurse -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($match) {
            return $match.FullName
        }
    }
    return $null
}

function Wait-ForWindow {
    param(
        [System.__ComObject]$Shell,
        [int]$ProcessId,
        [IntPtr]$WindowHandle,
        [string[]]$Titles,
        [int]$TimeoutSec
    )
    $deadline = (Get-Date).AddSeconds([Math]::Max(1, $TimeoutSec))
    while ((Get-Date) -lt $deadline) {
        try {
            if ($WindowHandle -ne [IntPtr]::Zero) {
                [void][NativeFocus]::ShowWindowAsync($WindowHandle, 9)
                if ([NativeFocus]::SetForegroundWindow($WindowHandle)) {
                    return $true
                }
            }
        } catch {
        }
        try {
            if ($ProcessId -gt 0 -and $Shell.AppActivate($ProcessId)) {
                return $true
            }
        } catch {
        }
        foreach ($title in $Titles) {
            if ([string]::IsNullOrWhiteSpace($title)) { continue }
            try {
                if ($Shell.AppActivate($title)) {
                    return $true
                }
            } catch {
            }
        }
        Start-Sleep -Milliseconds 700
    }
    return $false
}

function Test-WindowIsForeground {
    param(
        [IntPtr]$WindowHandle,
        [int]$ProcessId
    )

    try {
        $foreground = [NativeFocus]::GetForegroundWindow()
        if ($foreground -eq [IntPtr]::Zero) {
            return $false
        }
        if ($WindowHandle -ne [IntPtr]::Zero -and $foreground -eq $WindowHandle) {
            return $true
        }
        if ($ProcessId -gt 0) {
            $foregroundPid = [uint32]0
            [void][NativeFocus]::GetWindowThreadProcessId($foreground, [ref]$foregroundPid)
            if ([int]$foregroundPid -eq $ProcessId) {
                return $true
            }
        }
    } catch {
    }
    return $false
}

function Wait-ForForegroundWindow {
    param(
        [int]$ProcessId,
        [IntPtr]$WindowHandle,
        [int]$TimeoutSec = 3
    )

    $deadline = (Get-Date).AddSeconds([Math]::Max(1, $TimeoutSec))
    while ((Get-Date) -lt $deadline) {
        if ($WindowHandle -eq [IntPtr]::Zero -and $ProcessId -gt 0) {
            $WindowHandle = Get-ProcessWindowHandle -ProcessId $ProcessId
        }
        if (Test-WindowIsForeground -WindowHandle $WindowHandle -ProcessId $ProcessId) {
            return $true
        }
        Start-Sleep -Milliseconds 250
    }
    return $false
}

function Get-ProcessWindowHandle {
    param([int]$ProcessId)
    if ($ProcessId -le 0) {
        return [IntPtr]::Zero
    }
    try {
        $proc = Get-Process -Id $ProcessId -ErrorAction Stop
        return [IntPtr]$proc.MainWindowHandle
    } catch {
        return [IntPtr]::Zero
    }
}

function Get-WindowRect {
    param([IntPtr]$WindowHandle)
    if ($WindowHandle -eq [IntPtr]::Zero) {
        return $null
    }

    $rect = New-Object NativeFocus+RECT
    if (-not [NativeFocus]::GetWindowRect($WindowHandle, [ref]$rect)) {
        return $null
    }

    $width = [Math]::Max(1, $rect.Right - $rect.Left)
    $height = [Math]::Max(1, $rect.Bottom - $rect.Top)
    return @{
        Left = $rect.Left
        Top = $rect.Top
        Width = $width
        Height = $height
    }
}

function Invoke-WindowClick {
    param(
        [IntPtr]$WindowHandle,
        [double]$RelativeX,
        [double]$RelativeY,
        [int]$Clicks = 1
    )

    $rect = Get-WindowRect -WindowHandle $WindowHandle
    if ($null -eq $rect) {
        return $false
    }
    if (-not (Test-WindowIsForeground -WindowHandle $WindowHandle -ProcessId 0)) {
        return $false
    }

    $x = [int]($rect.Left + ($rect.Width * $RelativeX))
    $y = [int]($rect.Top + ($rect.Height * $RelativeY))
    [void][NativeFocus]::SetCursorPos($x, $y)
    foreach ($idx in 1..([Math]::Max(1, $Clicks))) {
        Start-Sleep -Milliseconds 140
        [NativeFocus]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
        Start-Sleep -Milliseconds 90
        [NativeFocus]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
        if ($idx -lt $Clicks) {
            Start-Sleep -Milliseconds 140
        }
    }
    Start-Sleep -Milliseconds 220
    return $true
}

function Set-ClipboardText {
    param([string]$Text)
    $value = if ($null -eq $Text) { "" } else { [string]$Text }
    [System.Windows.Forms.Clipboard]::SetText($value)
}

function Get-SendKeysLiteral {
    param([string]$Text)
    if ($null -eq $Text) {
        return ""
    }
    $chars = New-Object System.Collections.Generic.List[string]
    foreach ($char in ([string]$Text).ToCharArray()) {
        switch ($char) {
            '+' { [void]$chars.Add('{+}') }
            '^' { [void]$chars.Add('{^}') }
            '%' { [void]$chars.Add('{%}') }
            '~' { [void]$chars.Add('{~}') }
            '(' { [void]$chars.Add('{(}') }
            ')' { [void]$chars.Add('{)}') }
            '{' { [void]$chars.Add('{{}') }
            '}' { [void]$chars.Add('{}}') }
            '[' { [void]$chars.Add('{[}') }
            ']' { [void]$chars.Add('{]}') }
            default { [void]$chars.Add([string]$char) }
        }
    }
    return ($chars -join "")
}

function Paste-IntoFocusedField {
    param([string]$Text)
    $value = if ($null -eq $Text) { "" } else { [string]$Text }
    [System.Windows.Forms.SendKeys]::SendWait("^a")
    Start-Sleep -Milliseconds 90
    [System.Windows.Forms.SendKeys]::SendWait("{BACKSPACE}")
    Start-Sleep -Milliseconds 120
    $literal = Get-SendKeysLiteral -Text $value
    if (-not [string]::IsNullOrEmpty($literal)) {
        [System.Windows.Forms.SendKeys]::SendWait($literal)
    }
    Start-Sleep -Milliseconds 220
}

function Get-WindowPixelColor {
    param(
        [IntPtr]$WindowHandle,
        [double]$RelativeX,
        [double]$RelativeY
    )

    $rect = Get-WindowRect -WindowHandle $WindowHandle
    if ($null -eq $rect) {
        return $null
    }

    $x = [int]($rect.Left + ($rect.Width * $RelativeX))
    $y = [int]($rect.Top + ($rect.Height * $RelativeY))
    $bmp = New-Object System.Drawing.Bitmap 1, 1
    $graphics = [System.Drawing.Graphics]::FromImage($bmp)
    try {
        $graphics.CopyFromScreen($x, $y, 0, 0, (New-Object System.Drawing.Size 1, 1))
        return $bmp.GetPixel(0, 0)
    } finally {
        $graphics.Dispose()
        $bmp.Dispose()
    }
}

function Get-ColorBrightness {
    param($Color)
    if ($null -eq $Color) {
        return 0.0
    }
    return ([double]$Color.R + [double]$Color.G + [double]$Color.B) / 3.0
}

function Get-RegionAverageBrightness {
    param(
        [IntPtr]$WindowHandle,
        [double]$Left,
        [double]$Top,
        [double]$Right,
        [double]$Bottom,
        [int]$SamplesX = 6,
        [int]$SamplesY = 3
    )

    $sum = 0.0
    $count = 0
    for ($ix = 0; $ix -lt [Math]::Max(1, $SamplesX); $ix++) {
        for ($iy = 0; $iy -lt [Math]::Max(1, $SamplesY); $iy++) {
            $rx = $Left + (($Right - $Left) * (($ix + 0.5) / [Math]::Max(1, $SamplesX)))
            $ry = $Top + (($Bottom - $Top) * (($iy + 0.5) / [Math]::Max(1, $SamplesY)))
            $color = Get-WindowPixelColor -WindowHandle $WindowHandle -RelativeX $rx -RelativeY $ry
            $sum += Get-ColorBrightness -Color $color
            $count++
        }
    }
    if ($count -le 0) {
        return 0.0
    }
    return $sum / $count
}

function Is-IbApiSelected {
    param(
        [IntPtr]$WindowHandle,
        [hashtable]$Layout
    )

    $apiBrightness = Get-RegionAverageBrightness -WindowHandle $WindowHandle -Left $Layout.ApiRectLeft -Top $Layout.ApiRectTop -Right $Layout.ApiRectRight -Bottom $Layout.ApiRectBottom
    $fixBrightness = Get-RegionAverageBrightness -WindowHandle $WindowHandle -Left $Layout.FixRectLeft -Top $Layout.FixRectTop -Right $Layout.FixRectRight -Bottom $Layout.FixRectBottom
    return $apiBrightness -gt ($fixBrightness + 18.0)
}

function Is-PaperTradingSelected {
    param(
        [IntPtr]$WindowHandle,
        [hashtable]$Layout
    )

    $paperBrightness = Get-RegionAverageBrightness -WindowHandle $WindowHandle -Left $Layout.PaperRectLeft -Top $Layout.PaperRectTop -Right $Layout.PaperRectRight -Bottom $Layout.PaperRectBottom
    $liveBrightness = Get-RegionAverageBrightness -WindowHandle $WindowHandle -Left $Layout.LiveRectLeft -Top $Layout.LiveRectTop -Right $Layout.LiveRectRight -Bottom $Layout.LiveRectBottom
    return $paperBrightness -gt ($liveBrightness + 15.0)
}

function Try-SelectToggle {
    param(
        [IntPtr]$WindowHandle,
        [double]$BaseX,
        [double]$BaseY,
        [scriptblock]$Verifier
    )

    $xOffsets = @(0.00, -0.03, 0.03, -0.05, 0.05, -0.07, 0.07)
    $yOffsets = @(0.00, -0.02, 0.02, -0.035, 0.035)
    foreach ($yOffset in $yOffsets) {
        foreach ($xOffset in $xOffsets) {
            $targetX = [Math]::Min(0.95, [Math]::Max(0.05, $BaseX + $xOffset))
            $targetY = [Math]::Min(0.95, [Math]::Max(0.05, $BaseY + $yOffset))
            [void](Invoke-WindowClick -WindowHandle $WindowHandle -RelativeX $targetX -RelativeY $targetY -Clicks 2)
            Start-Sleep -Milliseconds 420
            if (& $Verifier) {
                return $true
            }
        }
    }
    return $false
}

function Get-LoginLayout {
    param([int]$Attempt)
    return @{
        ApiX = 0.630; ApiY = 0.252;
        PaperX = 0.630; PaperY = 0.375;
        UserX = 0.505; UserY = 0.647;
        PassX = 0.505; PassY = 0.716;
        LoginX = 0.502; LoginY = 0.804;
        AcceptX = 0.502; AcceptY = 0.848;
        FixRectLeft = 0.245; FixRectTop = 0.218; FixRectRight = 0.497; FixRectBottom = 0.275;
        ApiRectLeft = 0.505; ApiRectTop = 0.218; ApiRectRight = 0.748; ApiRectBottom = 0.275;
        LiveRectLeft = 0.245; LiveRectTop = 0.343; LiveRectRight = 0.497; LiveRectBottom = 0.398;
        PaperRectLeft = 0.505; PaperRectTop = 0.343; PaperRectRight = 0.748; PaperRectBottom = 0.398;
    }
}

function Try-AcceptPaperTradingWarning {
    param(
        [System.__ComObject]$Shell,
        [int]$ProcessId,
        [IntPtr]$WindowHandle,
        [hashtable]$Layout
    )

    if ($WindowHandle -eq [IntPtr]::Zero) {
        $WindowHandle = Get-ProcessWindowHandle -ProcessId $ProcessId
    }
    if ($WindowHandle -eq [IntPtr]::Zero) {
        return $false
    }
    if (-not (Wait-ForForegroundWindow -ProcessId $ProcessId -WindowHandle $WindowHandle -TimeoutSec 2)) {
        return $false
    }

    foreach ($offset in @(0.00, -0.02, 0.02)) {
        [void](Invoke-WindowClick -WindowHandle $WindowHandle -RelativeX $Layout.AcceptX -RelativeY ($Layout.AcceptY + $offset) -Clicks 2)
        Start-Sleep -Milliseconds 220
        [System.Windows.Forms.SendKeys]::SendWait(" ")
        Start-Sleep -Milliseconds 150
        [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
        Start-Sleep -Milliseconds 500
    }
    return $true
}

function Try-AcceptPaperTradingWarningIfPresent {
    param(
        [int]$ProcessId,
        [IntPtr]$WindowHandle
    )

    if ($ProcessId -le 0 -and $WindowHandle -eq [IntPtr]::Zero) {
        return $false
    }

    $shell = New-Object -ComObject WScript.Shell
    $layout = Get-LoginLayout -Attempt 1
    $accepted = $false
    foreach ($waitSeconds in @(1, 2, 2, 3)) {
        if (Try-AcceptPaperTradingWarning -Shell $shell -ProcessId $ProcessId -WindowHandle $WindowHandle -Layout $layout) {
            $accepted = $true
        }
        Start-Sleep -Seconds $waitSeconds
    }
    return $accepted
}

function Select-IbApiPaperMode {
    param(
        [IntPtr]$WindowHandle,
        [hashtable]$Layout
    )

    if ($WindowHandle -eq [IntPtr]::Zero) {
        return $false
    }

    $ok = $true
    if (-not (Is-IbApiSelected -WindowHandle $WindowHandle -Layout $Layout)) {
        $ok = (Try-SelectToggle -WindowHandle $WindowHandle -BaseX $Layout.ApiX -BaseY $Layout.ApiY -Verifier { Is-IbApiSelected -WindowHandle $WindowHandle -Layout $Layout }) -and $ok
    }
    if (-not (Is-PaperTradingSelected -WindowHandle $WindowHandle -Layout $Layout)) {
        $ok = (Try-SelectToggle -WindowHandle $WindowHandle -BaseX $Layout.PaperX -BaseY $Layout.PaperY -Verifier { Is-PaperTradingSelected -WindowHandle $WindowHandle -Layout $Layout }) -and $ok
    }
    return $ok -and (Is-IbApiSelected -WindowHandle $WindowHandle -Layout $Layout)
}

function Send-LoginKeys {
    param(
        [System.__ComObject]$Shell,
        [int]$ProcessId,
        [IntPtr]$WindowHandle,
        [string[]]$Titles,
        [string]$Username,
        [string]$Password,
        [int]$Attempt
    )

    if ($WindowHandle -eq [IntPtr]::Zero) {
        $WindowHandle = Get-ProcessWindowHandle -ProcessId $ProcessId
    }
    if (-not (Wait-ForForegroundWindow -ProcessId $ProcessId -WindowHandle $WindowHandle -TimeoutSec 2)) {
        return $false
    }
    $WindowHandle = Get-ProcessWindowHandle -ProcessId $ProcessId
    if (-not (Test-WindowIsForeground -WindowHandle $WindowHandle -ProcessId $ProcessId)) {
        return $false
    }

    $layout = Get-LoginLayout -Attempt $Attempt
    Start-Sleep -Milliseconds 800
    if (-not (Select-IbApiPaperMode -WindowHandle $WindowHandle -Layout $layout)) {
        Write-Status "IB API switch failed; login fields will not be touched"
        return $false
    }
    Start-Sleep -Milliseconds 900
    [void](Invoke-WindowClick -WindowHandle $WindowHandle -RelativeX $layout.UserX -RelativeY $layout.UserY -Clicks 2)
    Start-Sleep -Milliseconds 250
    Paste-IntoFocusedField -Text $Username
    [System.Windows.Forms.SendKeys]::SendWait("{TAB}")
    Start-Sleep -Milliseconds 220
    [void](Invoke-WindowClick -WindowHandle $WindowHandle -RelativeX $layout.PassX -RelativeY $layout.PassY -Clicks 1)
    Start-Sleep -Milliseconds 220
    Paste-IntoFocusedField -Text $Password
    [System.Windows.Forms.SendKeys]::SendWait("{TAB}")
    Start-Sleep -Milliseconds 220
    [void](Invoke-WindowClick -WindowHandle $WindowHandle -RelativeX $layout.LoginX -RelativeY $layout.LoginY -Clicks 1)
    Start-Sleep -Milliseconds 350
    [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
    Start-Sleep -Seconds 2
    [void](Try-AcceptPaperTradingWarning -Shell $Shell -ProcessId $ProcessId -WindowHandle $WindowHandle -Layout $layout)
    return $true
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = if (Test-Path (Join-Path $ProjectRoot "PRJCT")) { $ProjectRoot } elseif (Test-Path (Join-Path (Split-Path -Parent $ScriptDir) "PRJCT")) { Split-Path -Parent $ScriptDir } else { $ProjectRoot }
$ProjectDir = if (Test-Path (Join-Path $RepoRoot "PRJCT")) { Join-Path $RepoRoot "PRJCT" } else { $ScriptDir }
$envPath = Join-Path $ProjectDir "python-core\.env"
$envVars = Read-DotEnv -Path $envPath
$hostName = Get-FirstEnvValue -Values $envVars -Names @("IBKR_TWS_HOST")
if ([string]::IsNullOrWhiteSpace($hostName)) { $hostName = "127.0.0.1" }
$tradingMode = Normalize-TradingMode (Get-FirstEnvValue -Values $envVars -Names @("IBKR_GATEWAY_TRADING_MODE", "IBKR_TRADING_MODE"))
$defaultPort = if ($tradingMode -eq "live") { 4001 } else { 4002 }
$configuredPort = Get-IntValue (Get-FirstEnvValue -Values $envVars -Names @("IBKR_TWS_PORT")) $defaultPort
$username = Get-FirstEnvValue -Values $envVars -Names @("IBKR_GATEWAY_USERNAME", "IBKR_USERNAME", "USERNAME")
$password = Get-FirstEnvValue -Values $envVars -Names @("IBKR_GATEWAY_PASSWORD", "IBKR_PASSWORD", "PASSWORD")
$portCandidates = Get-PortCandidates -ConfiguredPort $configuredPort -TradingMode $tradingMode

Write-Status "ensure started | host=$hostName | configured_port=$configuredPort | mode=$tradingMode"

$openPort = Get-OpenPort -HostName $hostName -Ports $portCandidates
if ($null -ne $openPort) {
    $runningAtStart = @(Get-GatewayProcesses)
    if ($runningAtStart.Count -gt 0) {
        $startPid = [int]$runningAtStart[0].Id
        $startWindowHandle = [IntPtr]$runningAtStart[0].MainWindowHandle
        if (Try-AcceptPaperTradingWarningIfPresent -ProcessId $startPid -WindowHandle $startWindowHandle) {
            Write-Status "paper trading warning acceptance attempted"
        }
    }
    Write-Status "gateway API already reachable on port $openPort"
    exit 0
}

$gatewayExe = Find-GatewayExecutable
if (-not $gatewayExe) {
    Write-Status "gateway executable not found"
    exit 2
}

$gatewayDir = Split-Path -Parent $gatewayExe

$running = @(Get-GatewayProcesses)
$gatewayPid = 0
$gatewayWindowHandle = [IntPtr]::Zero
if ($running.Count -eq 0) {
    $proc = Start-Process -FilePath $gatewayExe -WorkingDirectory $gatewayDir -PassThru
    $gatewayPid = [int]$proc.Id
    Write-Status "gateway start requested (pid $($proc.Id))"
} else {
    $gatewayPid = [int]$running[0].Id
    $gatewayWindowHandle = [IntPtr]$running[0].MainWindowHandle
    Write-Status "gateway process already running (pid $($running[0].Id))"
}
if ($gatewayWindowHandle -eq [IntPtr]::Zero -and $gatewayPid -gt 0) {
    $windowDeadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $windowDeadline -and $gatewayWindowHandle -eq [IntPtr]::Zero) {
        Start-Sleep -Milliseconds 500
        $gatewayWindowHandle = Get-ProcessWindowHandle -ProcessId $gatewayPid
    }
}

$openPort = Get-OpenPort -HostName $hostName -Ports $portCandidates
if ($null -ne $openPort) {
    Write-Status "gateway API became reachable on port $openPort"
    exit 0
}

if ($SkipCredentialEntry) {
    Write-Status "skip credential entry requested and no API port is open"
    exit 3
}

if ([string]::IsNullOrWhiteSpace($username) -or [string]::IsNullOrWhiteSpace($password)) {
    Write-Status "missing gateway credentials in python-core/.env"
    exit 4
}

$shell = New-Object -ComObject WScript.Shell
$deadline = (Get-Date).AddSeconds([Math]::Max(30, $LoginTimeoutSec))
$attempt = 0

while ((Get-Date) -lt $deadline) {
    $openPort = Get-OpenPort -HostName $hostName -Ports $portCandidates
    if ($null -ne $openPort) {
        Write-Status "gateway login confirmed on port $openPort"
        exit 0
    }

    $attempt++
    if (-not (Wait-ForForegroundWindow -ProcessId $gatewayPid -WindowHandle $gatewayWindowHandle -TimeoutSec 2)) {
        Write-Status "login attempt $attempt skipped: gateway window is not foreground"
    } else {
        $layout = Get-LoginLayout -Attempt $attempt
        $switched = Select-IbApiPaperMode -WindowHandle $gatewayWindowHandle -Layout $layout
        if ($SwitchOnly) {
            if ($switched) {
                Write-Status "switch-only attempt $attempt confirmed"
                exit 0
            }
            Write-Status "switch-only attempt $attempt failed"
        }
        elseif (Send-LoginKeys -Shell $shell -ProcessId $gatewayPid -WindowHandle $gatewayWindowHandle -Titles @("IBKR Gateway", "IB Gateway", "Login") -Username $username -Password $password -Attempt $attempt) {
            Write-Status "login attempt $attempt sent"
        } else {
            Write-Status "login attempt $attempt skipped: gateway window is not foreground"
        }
    }

    for ($i = 0; $i -lt 10; $i++) {
        Start-Sleep -Seconds 2
        $layout = Get-LoginLayout -Attempt $attempt
        [void](Try-AcceptPaperTradingWarning -Shell $shell -ProcessId $gatewayPid -WindowHandle $gatewayWindowHandle -Layout $layout)
        $openPort = Get-OpenPort -HostName $hostName -Ports $portCandidates
        if ($null -ne $openPort) {
            Write-Status "gateway login confirmed on port $openPort"
            exit 0
        }
    }
}

Write-Status "gateway ensure timed out after $LoginTimeoutSec seconds"
exit 5
