param(
    [string]$ProjectRoot = "C:\aiinvest",
    [int]$LoginTimeoutSec = 150,
    [int]$ForegroundRetrySec = 30,
    [int]$ForegroundStableSec = 25,
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
using System.Text;

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

    [StructLayout(LayoutKind.Sequential)]
    public struct POINT
    {
        public int X;
        public int Y;
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

    [DllImport("user32.dll")]
    public static extern bool ScreenToClient(IntPtr hWnd, ref POINT lpPoint);

    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern IntPtr OpenInputDesktop(uint dwFlags, bool fInherit, uint dwDesiredAccess);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool CloseDesktop(IntPtr hDesktop);

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool GetUserObjectInformation(IntPtr hObj, int nIndex, StringBuilder pvInfo, int nLength, ref int lpnLengthNeeded);
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
    $result = New-Object System.Collections.Generic.List[object]
    $seen = New-Object System.Collections.Generic.HashSet[int]

    $native = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ProcessName -match "^(ibgateway|tws)$"
    } | Sort-Object StartTime -Descending)
    foreach ($proc in $native) {
        if ($seen.Add([int]$proc.Id)) {
            [void]$result.Add($proc)
        }
    }

    $javaGateway = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -match '^javaw?\.exe$' -and [string]$_.CommandLine -match 'ibcalpha\.ibc\.IbcGateway'
    })
    foreach ($procInfo in $javaGateway) {
        try {
            $proc = Get-Process -Id ([int]$procInfo.ProcessId) -ErrorAction Stop
            if ($seen.Add([int]$proc.Id)) {
                [void]$result.Add($proc)
            }
        } catch {
        }
    }

    return @($result | Sort-Object StartTime -Descending)
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
        $match = Get-ChildItem $root -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -in @("ibgateway.exe", "ibgateway1.exe") } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($match) {
            return $match.FullName
        }
    }
    return $null
}

function Stop-GatewayProcesses {
    param([int]$WaitMs = 15000)

    $running = @(Get-GatewayProcesses)
    foreach ($proc in $running) {
        try {
            Stop-Process -Id $proc.Id -Force -ErrorAction Stop
            Write-Status "stopped existing gateway process (pid $($proc.Id))"
        } catch {
            Write-Status "failed to stop gateway process pid $($proc.Id): $($_.Exception.Message)"
        }
    }

    if ($running.Count -eq 0) {
        return
    }

    $deadline = (Get-Date).AddMilliseconds([Math]::Max(1000, $WaitMs))
    while ((Get-Date) -lt $deadline) {
        if ((Get-GatewayProcesses).Count -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 300
    }
}

function Find-IbcRoot {
    param([hashtable]$EnvVars)

    $candidates = @(
        (Get-FirstEnvValue -Values $EnvVars -Names @("IBKR_IBC_PATH", "IBC_PATH")),
        "C:\IBC"
    )

    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        $root = $candidate.Trim()
        $startScript = Join-Path $root "scripts\StartIBC.bat"
        if (Test-Path $startScript) {
            return $root
        }
    }
    return $null
}

function Get-GatewayMajorVersion {
    param([string]$GatewayExecutable)

    if (-not [string]::IsNullOrWhiteSpace($GatewayExecutable)) {
        $folderName = Split-Path -Leaf (Split-Path -Parent $GatewayExecutable)
        if ($folderName -match '^\d+$') {
            return [int]$folderName
        }
    }

    $root = "C:\Jts\ibgateway"
    if (Test-Path $root) {
        $match = Get-ChildItem $root -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^\d+$' } |
            Sort-Object Name -Descending |
            Select-Object -First 1
        if ($match) {
            return [int]$match.Name
        }
    }
    return $null
}

function New-IbcConfigContent {
    param(
        [string]$Username,
        [string]$Password,
        [string]$TradingMode,
        [int]$ApiPort
    )

    return @(
        "# Generated by ensure_ibkr_gateway.ps1",
        "# This file is recreated from PRJCT/python-core/.env during recovery.",
        "FIX=no",
        "",
        "IbLoginId=$Username",
        "IbPassword=$Password",
        "TradingMode=$TradingMode",
        "AcceptNonBrokerageAccountWarning=yes",
        "LoginDialogDisplayTimeout=60",
        "OverrideTwsApiPort=$ApiPort",
        "ReadOnlyLogin=no",
        "ReloginAfterSecondFactorAuthenticationTimeout=yes",
        "SecondFactorAuthenticationExitInterval=60",
        "SecondFactorAuthenticationTimeout=180",
        ""
    )
}

function Ensure-IbcRuntimeConfig {
    param(
        [string]$ProjectDir,
        [string]$Username,
        [string]$Password,
        [string]$TradingMode,
        [int]$ApiPort
    )

    $runtimeDir = Join-Path $ProjectDir "_runtime\ibc"
    if (-not (Test-Path $runtimeDir)) {
        New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    }

    $configPath = Join-Path $runtimeDir "config.ini"
    $content = New-IbcConfigContent -Username $Username -Password $Password -TradingMode $TradingMode -ApiPort $ApiPort
    Set-Content -Path $configPath -Value $content -Encoding ASCII
    return $configPath
}

function Ensure-IbcRuntimeArtifacts {
    param(
        [string]$ProjectDir
    )

    $runtimeDir = Join-Path $ProjectDir "_runtime\ibc"
    $logDir = Join-Path $runtimeDir "Logs"
    if (-not (Test-Path $runtimeDir)) {
        New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    }
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }

    return @{
        RuntimeDir = $runtimeDir
        LogDir = $logDir
    }
}

function Get-IbcProgramPath {
    param(
        [int]$GatewayMajorVersion
    )

    $candidates = @(
        "C:\Jts\ibgateway\$GatewayMajorVersion",
        "C:\Jts\$GatewayMajorVersion"
    )

    foreach ($candidate in $candidates) {
        if (Test-Path (Join-Path $candidate "jars")) {
            return $candidate
        }
    }
    return $null
}

function Resolve-IbcJavaExe {
    param([string]$ProgramPath)

    $install4jPath = Join-Path $ProgramPath ".install4j"
    foreach ($cfgName in @("pref_jre.cfg", "inst_jre.cfg")) {
        $cfgPath = Join-Path $install4jPath $cfgName
        if (-not (Test-Path $cfgPath)) { continue }

        $basePath = ((Get-Content $cfgPath -ErrorAction SilentlyContinue | Select-Object -First 1) -as [string])
        if ([string]::IsNullOrWhiteSpace($basePath)) { continue }
        $javaExe = Join-Path ($basePath.Trim()) "bin\java.exe"
        if (Test-Path $javaExe) {
            return $javaExe
        }
    }

    $oracleJava = Join-Path $env:ProgramData "Oracle\Java\javapath\java.exe"
    if (Test-Path $oracleJava) {
        return $oracleJava
    }

    $javaCommand = Get-Command java.exe -ErrorAction SilentlyContinue
    if ($javaCommand -and -not [string]::IsNullOrWhiteSpace($javaCommand.Source)) {
        return $javaCommand.Source
    }

    return $null
}

function Get-IbcClasspath {
    param(
        [string]$ProgramPath,
        [string]$IbcRoot
    )

    $jarsPath = Join-Path $ProgramPath "jars"
    $jarFiles = @(Get-ChildItem $jarsPath -Filter "*.jar" -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object { $_.FullName })
    if ($jarFiles.Count -eq 0) {
        return $null
    }

    $parts = New-Object System.Collections.Generic.List[string]
    foreach ($jarFile in $jarFiles) {
        $parts.Add($jarFile)
    }
    $parts.Add((Join-Path $ProgramPath ".install4j\i4jruntime.jar"))
    $parts.Add((Join-Path $IbcRoot "IBC.jar"))
    return [string]::Join(';', $parts)
}

function Get-IbcVmOptions {
    param(
        [string]$ProgramPath,
        [string]$SettingsPath
    )

    $vmOptionsPath = Join-Path $ProgramPath "ibgateway.vmoptions"
    if (-not (Test-Path $vmOptionsPath)) {
        return @()
    }

    $options = New-Object System.Collections.Generic.List[string]
    foreach ($line in Get-Content $vmOptionsPath -ErrorAction SilentlyContinue) {
        $text = if ($null -eq $line) { "" } else { [string]$line }
        $text = $text.Trim()
        if ([string]::IsNullOrWhiteSpace($text) -or $text.StartsWith("#")) { continue }
        $options.Add($text)
    }

    $options.Add("-Dtwslaunch.autoupdate.serviceImpl=com.ib.tws.twslaunch.install4j.Install4jAutoUpdateService")
    $options.Add("-Dchannel=latest")
    $options.Add("-Dexe4j.isInstall4j=true")
    $options.Add("-Dinstall4jType=standalone")
    $options.Add("-DjtsConfigDir=$SettingsPath")
    $options.Add("-Dibcsessionid=$([int](Get-Random -Minimum 100000000 -Maximum 999999999))")

    return @($options)
}

function Get-IbcModuleAccessArgs {
    return @(
        "--add-opens=java.base/java.util=ALL-UNNAMED",
        "--add-opens=java.base/java.util.concurrent=ALL-UNNAMED",
        "--add-exports=java.base/sun.util=ALL-UNNAMED",
        "--add-exports=java.desktop/com.sun.java.swing.plaf.motif=ALL-UNNAMED",
        "--add-opens=java.desktop/java.awt=ALL-UNNAMED",
        "--add-opens=java.desktop/java.awt.dnd=ALL-UNNAMED",
        "--add-opens=java.desktop/javax.swing=ALL-UNNAMED",
        "--add-opens=java.desktop/javax.swing.event=ALL-UNNAMED",
        "--add-opens=java.desktop/javax.swing.plaf.basic=ALL-UNNAMED",
        "--add-opens=java.desktop/javax.swing.table=ALL-UNNAMED",
        "--add-opens=java.desktop/sun.awt=ALL-UNNAMED",
        "--add-exports=java.desktop/sun.swing=ALL-UNNAMED",
        "--add-opens=javafx.graphics/com.sun.javafx.application=ALL-UNNAMED",
        "--add-exports=javafx.media/com.sun.media.jfxmedia=ALL-UNNAMED",
        "--add-exports=javafx.media/com.sun.media.jfxmedia.events=ALL-UNNAMED",
        "--add-exports=javafx.media/com.sun.media.jfxmedia.locator=ALL-UNNAMED",
        "--add-exports=javafx.media/com.sun.media.jfxmediaimpl=ALL-UNNAMED",
        "--add-exports=javafx.web/com.sun.javafx.webkit=ALL-UNNAMED",
        "--add-opens=jdk.management/com.sun.management.internal=ALL-UNNAMED"
    )
}

function Start-GatewayViaIbc {
    param(
        [string]$ProjectDir,
        [string]$IbcRoot,
        [int]$GatewayMajorVersion,
        [string]$ConfigPath,
        [string]$TradingMode
    )

    $programPath = Get-IbcProgramPath -GatewayMajorVersion $GatewayMajorVersion
    if ([string]::IsNullOrWhiteSpace($programPath)) {
        throw "IBC program path for gateway version $GatewayMajorVersion was not found."
    }

    $javaExe = Resolve-IbcJavaExe -ProgramPath $programPath
    if ([string]::IsNullOrWhiteSpace($javaExe) -or -not (Test-Path $javaExe)) {
        throw "Java runtime for IBC gateway launch was not found."
    }

    $artifacts = Ensure-IbcRuntimeArtifacts -ProjectDir $ProjectDir
    $classpath = Get-IbcClasspath -ProgramPath $programPath -IbcRoot $IbcRoot
    if ([string]::IsNullOrWhiteSpace($classpath)) {
        throw "IBC classpath could not be constructed."
    }

    $settingsPath = $programPath
    $vmOptions = @(Get-IbcVmOptions -ProgramPath $programPath -SettingsPath $settingsPath)
    $moduleArgs = @(Get-IbcModuleAccessArgs)
    $stdoutPath = Join-Path $artifacts.LogDir "ibc-java-stdout.log"
    $stderrPath = Join-Path $artifacts.LogDir "ibc-java-stderr.log"

    $arguments = New-Object System.Collections.Generic.List[string]
    foreach ($arg in $moduleArgs) { $arguments.Add($arg) }
    foreach ($arg in $vmOptions) { $arguments.Add($arg) }
    $arguments.Add("-cp")
    $arguments.Add($classpath)
    $arguments.Add("ibcalpha.ibc.IbcGateway")
    $arguments.Add($ConfigPath)
    $arguments.Add($TradingMode)

    $originalJavaToolOptions = $env:JAVA_TOOL_OPTIONS
    $env:JAVA_TOOL_OPTIONS = ""
    try {
        $proc = Start-Process -FilePath $javaExe -ArgumentList @($arguments) -WorkingDirectory $settingsPath -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
    } finally {
        $env:JAVA_TOOL_OPTIONS = $originalJavaToolOptions
    }

    return @{
        Process = $proc
        LogDir = $artifacts.LogDir
        ProgramPath = $programPath
        SettingsPath = $settingsPath
        JavaExe = $javaExe
        StdoutPath = $stdoutPath
        StderrPath = $stderrPath
    }
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
                [System.Windows.Forms.SendKeys]::SendWait('%')
                Start-Sleep -Milliseconds 80
                if ([NativeFocus]::SetForegroundWindow($WindowHandle)) {
                    return $true
                }
            }
        } catch {
        }
        try {
            [System.Windows.Forms.SendKeys]::SendWait('%')
            Start-Sleep -Milliseconds 80
            if ($ProcessId -gt 0 -and $Shell.AppActivate($ProcessId)) {
                return $true
            }
        } catch {
        }
        foreach ($title in $Titles) {
            if ([string]::IsNullOrWhiteSpace($title)) { continue }
            try {
                [System.Windows.Forms.SendKeys]::SendWait('%')
                Start-Sleep -Milliseconds 80
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

function Get-DesktopState {
    $desktopHandle = [IntPtr]::Zero
    $desktopName = ""

    try {
        $desktopHandle = [NativeFocus]::OpenInputDesktop(0, $false, 0x0001)
        if ($desktopHandle -ne [IntPtr]::Zero) {
            $needed = 0
            $buffer = New-Object System.Text.StringBuilder 256
            if (-not [NativeFocus]::GetUserObjectInformation($desktopHandle, 2, $buffer, $buffer.Capacity, [ref]$needed) -and $needed -gt $buffer.Capacity) {
                $buffer = New-Object System.Text.StringBuilder ($needed + 1)
                [void][NativeFocus]::GetUserObjectInformation($desktopHandle, 2, $buffer, $buffer.Capacity, [ref]$needed)
            }
            $desktopName = $buffer.ToString().Trim()
        }
    } catch {
    } finally {
        if ($desktopHandle -ne [IntPtr]::Zero) {
            try { [void][NativeFocus]::CloseDesktop($desktopHandle) } catch { }
        }
    }

    $isLocked = $false
    if (-not [string]::IsNullOrWhiteSpace($desktopName)) {
        $isLocked = -not [string]::Equals($desktopName, "Default", [System.StringComparison]::OrdinalIgnoreCase)
    } else {
        $isLocked = @(Get-Process -Name "LogonUI" -ErrorAction SilentlyContinue).Count -gt 0
        if ($isLocked) {
            $desktopName = "LogonUI"
        }
    }

    if ([string]::IsNullOrWhiteSpace($desktopName)) {
        $desktopName = "unknown"
    }

    return @{
        DesktopName = $desktopName
        IsLocked = $isLocked
    }
}

function Request-ForegroundWindow {
    param(
        [System.__ComObject]$Shell,
        [int]$ProcessId,
        [IntPtr]$WindowHandle,
        [string[]]$Titles,
        [int]$ActivationTimeoutSec = 5
    )

    if ($WindowHandle -eq [IntPtr]::Zero) {
        $WindowHandle = Get-ProcessWindowHandle -ProcessId $ProcessId
    }

    $attempted = $false
    if ($WindowHandle -ne [IntPtr]::Zero -or $ProcessId -gt 0) {
        $attempted = Wait-ForWindow -Shell $Shell -ProcessId $ProcessId -WindowHandle $WindowHandle -Titles $Titles -TimeoutSec $ActivationTimeoutSec
    }

    $WindowHandle = Get-ProcessWindowHandle -ProcessId $ProcessId
    $isForeground = $false
    if ($WindowHandle -ne [IntPtr]::Zero -or $ProcessId -gt 0) {
        $isForeground = Wait-ForForegroundWindow -ProcessId $ProcessId -WindowHandle $WindowHandle -TimeoutSec 2
    }

    return @{
        Attempted = $attempted
        WindowHandle = $WindowHandle
        IsForeground = $isForeground
    }
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

function Convert-ScreenToClientPoint {
    param(
        [IntPtr]$WindowHandle,
        [int]$ScreenX,
        [int]$ScreenY
    )

    if ($WindowHandle -eq [IntPtr]::Zero) {
        return $null
    }

    $point = New-Object NativeFocus+POINT
    $point.X = $ScreenX
    $point.Y = $ScreenY
    if (-not [NativeFocus]::ScreenToClient($WindowHandle, [ref]$point)) {
        return $null
    }

    return @{
        X = [int]$point.X
        Y = [int]$point.Y
    }
}

function New-LParamFromPoint {
    param(
        [int]$X,
        [int]$Y
    )

    $value = (($Y -band 0xFFFF) -shl 16) -bor ($X -band 0xFFFF)
    return [IntPtr]$value
}

function Invoke-DirectWindowClick {
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

    $screenX = [int]($rect.Left + ($rect.Width * $RelativeX))
    $screenY = [int]($rect.Top + ($rect.Height * $RelativeY))
    $clientPoint = Convert-ScreenToClientPoint -WindowHandle $WindowHandle -ScreenX $screenX -ScreenY $screenY
    if ($null -eq $clientPoint) {
        return $false
    }

    $lParam = New-LParamFromPoint -X $clientPoint.X -Y $clientPoint.Y
    foreach ($idx in 1..([Math]::Max(1, $Clicks))) {
        [void][NativeFocus]::PostMessage($WindowHandle, 0x0200, [IntPtr]::Zero, $lParam)
        Start-Sleep -Milliseconds 60
        [void][NativeFocus]::PostMessage($WindowHandle, 0x0201, [IntPtr]1, $lParam)
        Start-Sleep -Milliseconds 70
        [void][NativeFocus]::PostMessage($WindowHandle, 0x0202, [IntPtr]::Zero, $lParam)
        if ($idx -lt $Clicks) {
            Start-Sleep -Milliseconds 120
        }
    }
    Start-Sleep -Milliseconds 220
    return $true
}

function Send-WindowVirtualKey {
    param(
        [IntPtr]$WindowHandle,
        [int]$VirtualKey,
        [int]$CharCode = 0
    )

    if ($WindowHandle -eq [IntPtr]::Zero) {
        return $false
    }

    [void][NativeFocus]::PostMessage($WindowHandle, 0x0100, [IntPtr]$VirtualKey, [IntPtr]::Zero)
    Start-Sleep -Milliseconds 35
    if ($CharCode -gt 0) {
        [void][NativeFocus]::PostMessage($WindowHandle, 0x0102, [IntPtr]$CharCode, [IntPtr]::Zero)
        Start-Sleep -Milliseconds 35
    }
    [void][NativeFocus]::PostMessage($WindowHandle, 0x0101, [IntPtr]$VirtualKey, [IntPtr]::Zero)
    Start-Sleep -Milliseconds 70
    return $true
}

function Send-WindowText {
    param(
        [IntPtr]$WindowHandle,
        [string]$Text
    )

    if ($WindowHandle -eq [IntPtr]::Zero) {
        return $false
    }

    $value = if ($null -eq $Text) { "" } else { [string]$Text }
    foreach ($char in $value.ToCharArray()) {
        [void][NativeFocus]::PostMessage($WindowHandle, 0x0102, [IntPtr][int][char]$char, [IntPtr]::Zero)
        Start-Sleep -Milliseconds 25
    }
    Start-Sleep -Milliseconds 160
    return $true
}

function Invoke-WindowClick {
    param(
        [IntPtr]$WindowHandle,
        [double]$RelativeX,
        [double]$RelativeY,
        [int]$Clicks = 1,
        [switch]$SkipForegroundCheck
    )

    $rect = Get-WindowRect -WindowHandle $WindowHandle
    if ($null -eq $rect) {
        return $false
    }
    if (-not $SkipForegroundCheck -and -not (Test-WindowIsForeground -WindowHandle $WindowHandle -ProcessId 0)) {
        return $false
    }

    if ($SkipForegroundCheck) {
        return Invoke-DirectWindowClick -WindowHandle $WindowHandle -RelativeX $RelativeX -RelativeY $RelativeY -Clicks $Clicks
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
    param(
        [string]$Text,
        [IntPtr]$WindowHandle = [IntPtr]::Zero,
        [switch]$DirectInput
    )

    if ($DirectInput -and $WindowHandle -ne [IntPtr]::Zero) {
        for ($idx = 0; $idx -lt 80; $idx++) {
            [void](Send-WindowVirtualKey -WindowHandle $WindowHandle -VirtualKey 0x08 -CharCode 0x08)
        }
        return (Send-WindowText -WindowHandle $WindowHandle -Text $Text)
    }

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
        [scriptblock]$Verifier,
        [switch]$SkipForegroundCheck
    )

    $xOffsets = @(0.00, -0.03, 0.03, -0.05, 0.05, -0.07, 0.07)
    $yOffsets = @(0.00, -0.02, 0.02, -0.035, 0.035)
    foreach ($yOffset in $yOffsets) {
        foreach ($xOffset in $xOffsets) {
            $targetX = [Math]::Min(0.95, [Math]::Max(0.05, $BaseX + $xOffset))
            $targetY = [Math]::Min(0.95, [Math]::Max(0.05, $BaseY + $yOffset))
            [void](Invoke-WindowClick -WindowHandle $WindowHandle -RelativeX $targetX -RelativeY $targetY -Clicks 2 -SkipForegroundCheck:$SkipForegroundCheck)
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
        [hashtable]$Layout,
        [switch]$SkipForegroundCheck
    )

    if ($WindowHandle -eq [IntPtr]::Zero) {
        $WindowHandle = Get-ProcessWindowHandle -ProcessId $ProcessId
    }
    if ($WindowHandle -eq [IntPtr]::Zero) {
        return $false
    }
    if (-not $SkipForegroundCheck -and -not (Wait-ForForegroundWindow -ProcessId $ProcessId -WindowHandle $WindowHandle -TimeoutSec 2)) {
        return $false
    }

    foreach ($offset in @(0.00, -0.02, 0.02)) {
        [void](Invoke-WindowClick -WindowHandle $WindowHandle -RelativeX $Layout.AcceptX -RelativeY ($Layout.AcceptY + $offset) -Clicks 2 -SkipForegroundCheck:$SkipForegroundCheck)
        Start-Sleep -Milliseconds 220
        if ($SkipForegroundCheck) {
            [void](Send-WindowVirtualKey -WindowHandle $WindowHandle -VirtualKey 0x20 -CharCode 0x20)
            [void](Send-WindowVirtualKey -WindowHandle $WindowHandle -VirtualKey 0x0D -CharCode 0x0D)
        } else {
            [System.Windows.Forms.SendKeys]::SendWait(" ")
            Start-Sleep -Milliseconds 150
            [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
        }
        Start-Sleep -Milliseconds 500
    }
    return $true
}

function Try-AcceptPaperTradingWarningIfPresent {
    param(
        [int]$ProcessId,
        [IntPtr]$WindowHandle,
        [switch]$SkipForegroundCheck
    )

    if ($ProcessId -le 0 -and $WindowHandle -eq [IntPtr]::Zero) {
        return $false
    }

    $shell = New-Object -ComObject WScript.Shell
    $layout = Get-LoginLayout -Attempt 1
    $accepted = $false
    foreach ($waitSeconds in @(1, 2, 2, 3)) {
        if (Try-AcceptPaperTradingWarning -Shell $shell -ProcessId $ProcessId -WindowHandle $WindowHandle -Layout $layout -SkipForegroundCheck:$SkipForegroundCheck) {
            $accepted = $true
        }
        Start-Sleep -Seconds $waitSeconds
    }
    return $accepted
}

function Select-IbApiPaperMode {
    param(
        [IntPtr]$WindowHandle,
        [hashtable]$Layout,
        [switch]$SkipForegroundCheck
    )

    if ($WindowHandle -eq [IntPtr]::Zero) {
        return $false
    }

    $ok = $true
    if (-not (Is-IbApiSelected -WindowHandle $WindowHandle -Layout $Layout)) {
        $ok = (Try-SelectToggle -WindowHandle $WindowHandle -BaseX $Layout.ApiX -BaseY $Layout.ApiY -Verifier { Is-IbApiSelected -WindowHandle $WindowHandle -Layout $Layout } -SkipForegroundCheck:$SkipForegroundCheck) -and $ok
    }
    if (-not (Is-PaperTradingSelected -WindowHandle $WindowHandle -Layout $Layout)) {
        $ok = (Try-SelectToggle -WindowHandle $WindowHandle -BaseX $Layout.PaperX -BaseY $Layout.PaperY -Verifier { Is-PaperTradingSelected -WindowHandle $WindowHandle -Layout $Layout } -SkipForegroundCheck:$SkipForegroundCheck) -and $ok
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
        [int]$Attempt,
        [switch]$SkipForegroundCheck
    )

    if ($WindowHandle -eq [IntPtr]::Zero) {
        $WindowHandle = Get-ProcessWindowHandle -ProcessId $ProcessId
    }
    if (-not $SkipForegroundCheck -and -not (Wait-ForForegroundWindow -ProcessId $ProcessId -WindowHandle $WindowHandle -TimeoutSec 2)) {
        return $false
    }
    $WindowHandle = Get-ProcessWindowHandle -ProcessId $ProcessId
    if (-not $SkipForegroundCheck -and -not (Test-WindowIsForeground -WindowHandle $WindowHandle -ProcessId $ProcessId)) {
        return $false
    }

    $layout = Get-LoginLayout -Attempt $Attempt
    Start-Sleep -Milliseconds 800
    if (-not (Select-IbApiPaperMode -WindowHandle $WindowHandle -Layout $layout -SkipForegroundCheck:$SkipForegroundCheck)) {
        Write-Status "IB API switch failed; login fields will not be touched"
        return $false
    }
    Start-Sleep -Milliseconds 900
    [void](Invoke-WindowClick -WindowHandle $WindowHandle -RelativeX $layout.UserX -RelativeY $layout.UserY -Clicks 2 -SkipForegroundCheck:$SkipForegroundCheck)
    Start-Sleep -Milliseconds 250
    [void](Paste-IntoFocusedField -Text $Username -WindowHandle $WindowHandle -DirectInput:$SkipForegroundCheck)
    if ($SkipForegroundCheck) {
        [void](Send-WindowVirtualKey -WindowHandle $WindowHandle -VirtualKey 0x09 -CharCode 0x09)
    } else {
        [System.Windows.Forms.SendKeys]::SendWait("{TAB}")
    }
    Start-Sleep -Milliseconds 220
    [void](Invoke-WindowClick -WindowHandle $WindowHandle -RelativeX $layout.PassX -RelativeY $layout.PassY -Clicks 1 -SkipForegroundCheck:$SkipForegroundCheck)
    Start-Sleep -Milliseconds 220
    [void](Paste-IntoFocusedField -Text $Password -WindowHandle $WindowHandle -DirectInput:$SkipForegroundCheck)
    if ($SkipForegroundCheck) {
        [void](Send-WindowVirtualKey -WindowHandle $WindowHandle -VirtualKey 0x09 -CharCode 0x09)
    } else {
        [System.Windows.Forms.SendKeys]::SendWait("{TAB}")
    }
    Start-Sleep -Milliseconds 220
    [void](Invoke-WindowClick -WindowHandle $WindowHandle -RelativeX $layout.LoginX -RelativeY $layout.LoginY -Clicks 1 -SkipForegroundCheck:$SkipForegroundCheck)
    Start-Sleep -Milliseconds 350
    if ($SkipForegroundCheck) {
        [void](Send-WindowVirtualKey -WindowHandle $WindowHandle -VirtualKey 0x0D -CharCode 0x0D)
    } else {
        [System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
    }
    Start-Sleep -Seconds 2
    [void](Try-AcceptPaperTradingWarning -Shell $Shell -ProcessId $ProcessId -WindowHandle $WindowHandle -Layout $layout -SkipForegroundCheck:$SkipForegroundCheck)
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
$ibcRoot = Find-IbcRoot -EnvVars $envVars

Write-Status "ensure started | host=$hostName | configured_port=$configuredPort | mode=$tradingMode"
if ($ibcRoot) {
    Write-Status "IBC detected at $ibcRoot"
}

$openPort = Get-OpenPort -HostName $hostName -Ports $portCandidates
if ($null -ne $openPort) {
    $runningAtStart = @(Get-GatewayProcesses)
    if ($runningAtStart.Count -gt 0) {
        $startPid = [int]$runningAtStart[0].Id
        $startWindowHandle = [IntPtr]$runningAtStart[0].MainWindowHandle
        $startupDesktopState = Get-DesktopState
        if (Try-AcceptPaperTradingWarningIfPresent -ProcessId $startPid -WindowHandle $startWindowHandle -SkipForegroundCheck:$startupDesktopState.IsLocked) {
            Write-Status "paper trading warning acceptance attempted"
        }
    }
    Write-Status "gateway API already reachable on port $openPort"
    exit 0
}

$gatewayExe = Find-GatewayExecutable
$gatewayDir = if ($gatewayExe) { Split-Path -Parent $gatewayExe } else { $null }
$gatewayMajorVersion = Get-GatewayMajorVersion -GatewayExecutable $gatewayExe

if (-not $SkipCredentialEntry -and -not $SwitchOnly -and -not [string]::IsNullOrWhiteSpace($ibcRoot)) {
    if ([string]::IsNullOrWhiteSpace($username) -or [string]::IsNullOrWhiteSpace($password)) {
        Write-Status "IBC startup skipped: missing gateway credentials in python-core/.env"
    } elseif ($null -eq $gatewayMajorVersion) {
        Write-Status "IBC startup skipped: gateway major version not detected"
    } else {
        $ibcConfigPath = Ensure-IbcRuntimeConfig -ProjectDir $ProjectDir -Username $username -Password $password -TradingMode $tradingMode -ApiPort $configuredPort
        Write-Status "IBC runtime config prepared at $ibcConfigPath"

        Stop-GatewayProcesses
        Start-Sleep -Seconds 1

        try {
            $ibcLaunch = Start-GatewayViaIbc -ProjectDir $ProjectDir -IbcRoot $ibcRoot -GatewayMajorVersion $gatewayMajorVersion -ConfigPath $ibcConfigPath -TradingMode $tradingMode
            if ($null -ne $ibcLaunch -and $null -ne $ibcLaunch.Process) {
                Write-Status "IBC gateway start requested (pid $($ibcLaunch.Process.Id), version $gatewayMajorVersion)"
                Write-Status "IBC launch context | program=$($ibcLaunch.ProgramPath) | settings=$($ibcLaunch.SettingsPath) | java=$($ibcLaunch.JavaExe)"
                $ibcDeadline = (Get-Date).AddSeconds([Math]::Min([Math]::Max(30, $LoginTimeoutSec), 90))
                while ((Get-Date) -lt $ibcDeadline) {
                    $openPort = Get-OpenPort -HostName $hostName -Ports $portCandidates
                    if ($null -ne $openPort) {
                        Write-Status "gateway login confirmed by IBC on port $openPort"
                        exit 0
                    }
                    Start-Sleep -Seconds 2
                }
                Write-Status "IBC did not confirm API availability within startup window; logs at $($ibcLaunch.LogDir); stderr=$($ibcLaunch.StderrPath); falling back to legacy recovery"
            } else {
                Write-Status "IBC gateway start could not be launched; falling back to legacy recovery"
            }
        } catch {
            Write-Status "IBC startup failed: $($_.Exception.Message); falling back to legacy recovery"
        }
    }
}

if (-not $gatewayExe) {
    Write-Status "gateway executable not found"
    exit 2
}

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
$activationTitles = @("IBKR Gateway", "IB Gateway", "Login")
$foregroundRetrySeconds = [Math]::Max(5, $ForegroundRetrySec)
$foregroundStableSeconds = [Math]::Max(0, $ForegroundStableSec)
$lastForegroundSwitchUtc = $null
$nextForegroundRequestUtc = [DateTime]::MinValue
$foregroundWaitLogged = $false
$lastDesktopLocked = $null

while ((Get-Date) -lt $deadline) {
    $now = Get-Date
    $openPort = Get-OpenPort -HostName $hostName -Ports $portCandidates
    if ($null -ne $openPort) {
        Write-Status "gateway login confirmed on port $openPort"
        exit 0
    }

    if ($gatewayWindowHandle -eq [IntPtr]::Zero -and $gatewayPid -gt 0) {
        $gatewayWindowHandle = Get-ProcessWindowHandle -ProcessId $gatewayPid
    }

    $desktopState = Get-DesktopState
    $skipForegroundChecks = $desktopState.IsLocked
    if ($lastDesktopLocked -ne $skipForegroundChecks) {
        if ($skipForegroundChecks) {
            Write-Status "desktop '$($desktopState.DesktopName)' detected; skipping foreground checks while workstation is locked"
        } else {
            Write-Status "desktop '$($desktopState.DesktopName)' detected; foreground checks enabled"
        }
        $lastDesktopLocked = $skipForegroundChecks
    }

    if (-not $skipForegroundChecks) {
        $isForeground = Test-WindowIsForeground -WindowHandle $gatewayWindowHandle -ProcessId $gatewayPid
        if (-not $isForeground) {
            $foregroundWaitLogged = $false
            if ($now -ge $nextForegroundRequestUtc) {
                $request = Request-ForegroundWindow -Shell $shell -ProcessId $gatewayPid -WindowHandle $gatewayWindowHandle -Titles $activationTitles -ActivationTimeoutSec 5
                $gatewayWindowHandle = $request.WindowHandle
                $lastForegroundSwitchUtc = Get-Date
                $nextForegroundRequestUtc = $lastForegroundSwitchUtc.AddSeconds($foregroundRetrySeconds)
                if ($request.Attempted) {
                    if ($request.IsForeground) {
                        Write-Status "gateway window switched to foreground; waiting $foregroundStableSeconds seconds before login"
                    } else {
                        Write-Status "gateway window focus requested; waiting for foreground"
                    }
                } else {
                    Write-Status "gateway window focus requested; window handle is not ready yet"
                }
            }
            Start-Sleep -Seconds 1
            continue
        }

        if ($null -ne $lastForegroundSwitchUtc) {
            $stableForSec = ($now - $lastForegroundSwitchUtc).TotalSeconds
            if ($stableForSec -lt $foregroundStableSeconds) {
                if (-not $foregroundWaitLogged) {
                    $remaining = [Math]::Ceiling($foregroundStableSeconds - $stableForSec)
                    Write-Status "gateway window is foreground; waiting ${remaining}s before login"
                    $foregroundWaitLogged = $true
                }
                Start-Sleep -Seconds 1
                continue
            }
        }
    } else {
        $foregroundWaitLogged = $false
    }
    $foregroundWaitLogged = $false

    $attempt++
    $layout = Get-LoginLayout -Attempt $attempt
    $switched = Select-IbApiPaperMode -WindowHandle $gatewayWindowHandle -Layout $layout -SkipForegroundCheck:$skipForegroundChecks
    if ($SwitchOnly) {
        if ($switched) {
            Write-Status "switch-only attempt $attempt confirmed"
            exit 0
        }
        Write-Status "switch-only attempt $attempt failed"
        $lastForegroundSwitchUtc = Get-Date
        $nextForegroundRequestUtc = $lastForegroundSwitchUtc.AddSeconds($foregroundRetrySeconds)
        Start-Sleep -Seconds 1
        continue
    }

    if (Send-LoginKeys -Shell $shell -ProcessId $gatewayPid -WindowHandle $gatewayWindowHandle -Titles $activationTitles -Username $username -Password $password -Attempt $attempt -SkipForegroundCheck:$skipForegroundChecks) {
        Write-Status "login attempt $attempt sent"
        for ($i = 0; $i -lt 10; $i++) {
            Start-Sleep -Seconds 2
            $layout = Get-LoginLayout -Attempt $attempt
            [void](Try-AcceptPaperTradingWarning -Shell $shell -ProcessId $gatewayPid -WindowHandle $gatewayWindowHandle -Layout $layout -SkipForegroundCheck:$skipForegroundChecks)
            $openPort = Get-OpenPort -HostName $hostName -Ports $portCandidates
            if ($null -ne $openPort) {
                Write-Status "gateway login confirmed on port $openPort"
                exit 0
            }
        }
    } else {
        if ($skipForegroundChecks) {
            Write-Status "login attempt $attempt failed before key entry in locked-session mode"
        } else {
            Write-Status "login attempt $attempt deferred: gateway window lost foreground"
        }
    }

    $lastForegroundSwitchUtc = Get-Date
    $nextForegroundRequestUtc = $lastForegroundSwitchUtc.AddSeconds($foregroundRetrySeconds)
    Start-Sleep -Seconds 1
}

Write-Status "gateway ensure timed out after $LoginTimeoutSec seconds"
exit 5
