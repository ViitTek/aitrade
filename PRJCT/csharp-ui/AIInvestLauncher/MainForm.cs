using Microsoft.Web.WebView2.WinForms;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Management;
using System.Net;
using System.Net.Http;
using System.Net.NetworkInformation;
using System.Threading.Tasks;
using System.Windows.Forms;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Text.Json;
using System.Threading;
using Microsoft.Win32;
using System.Linq;

namespace AIInvestLauncher;

public sealed class MainForm : Form
{
    private const bool EmbeddedDashboardEnabled = false;
    private const int DefaultWatchdogAutostartDelayMs = 60000;
    private const int DeferredWatchdogAutostartDelayMs = 10000;
    private const int IbkrGatewayRecoveryCooldownSeconds = 180;
    private static readonly Regex AnsiEscapeRegex = new(@"\x1B\[[0-9;?]*[ -/]*[@-~]", RegexOptions.Compiled);
    private static readonly Color Bg = Color.FromArgb(14, 16, 20);
    private static readonly Color PanelBg = Color.FromArgb(24, 27, 33);
    private static readonly Color Border = Color.FromArgb(58, 63, 72);
    private static readonly Color Fg = Color.FromArgb(229, 231, 235);
    private static readonly Color Muted = Color.FromArgb(156, 163, 175);
    private static readonly Color CardBg = Color.FromArgb(22, 25, 31);
    private static readonly Color CardBorder = Color.FromArgb(49, 54, 62);
    private static readonly Color InputFill = Color.FromArgb(46, 50, 58);
    private static readonly Color InputBorder = Color.FromArgb(64, 70, 80);

    private readonly TextBox _txtRoot = new() { Text = @"C:\aiinvest", Width = 420 };
    private readonly TextBox _txtApiPort = new() { Text = "8010", Width = 70 };
    private readonly TextBox _txtBacktestPort = new() { Text = "8001", Width = 70 };
    private readonly TextBox _txtDashPort = new() { Text = "5173", Width = 70 };
    private readonly CheckBox _chkAutoStartBot = new() { Text = "Auto start bot", Checked = true, AutoSize = true };
    private readonly BadgeLabel _lblApi = NewBadge();
    private readonly BadgeLabel _lblBot = NewBadge();
    private readonly BadgeLabel _lblDash = NewBadge();
    private readonly BadgeLabel _lblApiPort = NewBadge();
    private readonly BadgeLabel _lblBacktestPort = NewBadge();
    private readonly BadgeLabel _lblDashPort = NewBadge();
    private readonly BadgeLabel _lblCollector = NewBadge();
    private readonly BadgeLabel _lblNews = NewBadge();
    private readonly BadgeLabel _lblMarketData = NewBadge();
    private readonly BadgeLabel _lblMarketIntel = NewBadge();
    private readonly BadgeLabel _lblIbkr = NewBadge();
    private readonly BadgeLabel _lblTest = NewBadge();
    private readonly BadgeLabel _lblWatchdog = NewBadge();
    private readonly List<BadgeLabel> _statusBadges = new();
    private readonly Button _btnProjectAction = new() { Text = "Start Project", Width = 130 };
    private readonly Button _btnTopIbkr = new();
    private readonly Button _btnTopTest = new();
    private readonly Button _btnTopWatchdog = new();
    private TableLayoutPanel? _statusGrid;
    private readonly Panel _busyBar = new()
    {
        Visible = false,
        Height = 10,
        Dock = DockStyle.Top,
        BackColor = Color.FromArgb(20, 24, 31)
    };
    private readonly Panel _busyPulse = new()
    {
        Width = 180,
        Height = 10,
        Left = -180,
        Top = 0,
        BackColor = Color.FromArgb(56, 189, 248)
    };
    private readonly System.Windows.Forms.Timer _busyTimer = new() { Interval = 16 };
    private int _busyPulseX = -180;

    private readonly RichTextBox _logDashboard = NewLogBox();
    private readonly RichTextBox _logAutoTune = NewLogBox();
    private readonly RichTextBox _logShadowReport = NewLogBox();
    private readonly RichTextBox _logControlRuntime = NewLogBox();
    private readonly RichTextBox _logLauncher = NewLogBox();
    private readonly RichTextBox _logTradedSymbols = NewLogBox();
    private readonly RichTextBox _logIbkr = NewLogBox();
    private readonly RichTextBox _logTest = NewLogBox();
    private readonly RichTextBox _logWatchdog = NewLogBox();
    private readonly RichTextBox _logRunner = NewLogBox();
    private readonly RichTextBox _logIbkrTest = NewLogBox();
    private readonly RichTextBox _logIbkrWatchdog = NewLogBox();
    private readonly RichTextBox _logIbkrRunner = NewLogBox();
    private readonly ComboBox _cmbShadowActions = new() { DropDownStyle = ComboBoxStyle.DropDownList, Width = 280 };

    private readonly WebView2 _web = new() { Dock = DockStyle.Fill };
    private readonly System.Windows.Forms.Timer _healthTimer = new() { Interval = 3000 };
    private readonly System.Windows.Forms.Timer _watchdogAutostartTimer = new() { Interval = DefaultWatchdogAutostartDelayMs };
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(8) };

    private Process? _apiProc;
    private Process? _backtestProc;
    private Process? _collectorProc;
    private Process? _newsProc;
    private Process? _marketDataProc;
    private Process? _marketIntelProc;
    private Process? _dashboardProc;
    private Process? _testProc;
    private Process? _watchdogProc;
    private Process? _ibkrWatchdogProc;
    private bool _healthRefreshInProgress;
    private readonly Dictionary<string, Button> _tabButtons = new();
    private readonly Dictionary<string, TabStateTone> _tabStates = new();
    private string _activeTabTitle = "Control";
    private string? _lastAutoTuneCreatedAt;
    private bool _busy;
    private bool _isPseudoMaximized;
    private Rectangle _restoreBounds;
    private readonly string _launcherLogDir;
    private readonly string _launcherLogPath;
    private readonly object _fileLogLock = new();
    private bool? _lastApiOk;
    private bool? _lastDashOk;
    private int _projectActionInProgress;
    private int _botAutostartInProgress;
    private int _ibkrGatewayEnsureInProgress;
    private long _projectActionId;
    private int _apiFailStreak;
    private int _dashFailStreak;
    private DateTime _lastDashboardStartAttemptUtc = DateTime.MinValue;
    private DateTime _lastWebReconnectAttemptUtc = DateTime.MinValue;
    private string? _lastShadowReportPayload;
    private DateTime _lastAutoTuneFetchUtc = DateTime.MinValue;
    private DateTime _lastShadowFetchUtc = DateTime.MinValue;
    private DateTime _lastTradedSymbolsFetchUtc = DateTime.MinValue;
    private DateTime _lastIbkrGatewayEnsureUtc = DateTime.MinValue;
    private readonly Dictionary<string, string?> _lastWatchdogRuntimeLines = new();
    private readonly Dictionary<string, string?> _lastTestHeartbeatRuntimeKeys = new();
    private string? _lastIbkrStatusLine;
    private readonly Dictionary<string, string?> _lastRunnerRuntimeLines = new();
    private readonly Dictionary<string, string?> _lastRuntimeDiagnosticLines = new();
    private const int AutoTuneFetchSeconds = 30;
    private const int ShadowFetchSeconds = 60;
    private const int TradedSymbolsFetchSeconds = 60;
    private string _shadowActionsParam = "shadow";
    private const int DWMWA_USE_IMMERSIVE_DARK_MODE = 20;

    public MainForm()
    {
        _launcherLogDir = Path.Combine(AppContext.BaseDirectory, "logs", "launcher");
        Directory.CreateDirectory(_launcherLogDir);
        _launcherLogPath = Path.Combine(_launcherLogDir, $"launcher-{DateTime.Now:yyyy-MM-dd}.log");
        WriteFileLog("=== Launcher started ===");
        HookLifecycleLogging();

        Text = "AIInvest Launcher";
        Width = 1460;
        Height = 900;
        MinimumSize = new Size(980, 620);
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Bg;
        ForeColor = Fg;
        FormBorderStyle = FormBorderStyle.Sizable;
        MaximizeBox = true;
        MinimizeBox = true;
        Padding = new Padding(0);
        ResizeRedraw = true;
        Region = null;
        _restoreBounds = Bounds;

        var pages = new List<(string Title, Control Content)>
        {
            ("Control", BuildControlPage()),
            ("Dashboard", BuildDashboardPage()),
            ("Dashboard Log", BuildLogPage(_logDashboard)),
            ("Auto Tune Log", BuildLogPage(_logAutoTune)),
            ("Shadow Report", BuildShadowReportPage()),
            ("Launcher Log", BuildLogPage(_logLauncher)),
            ("Traded Symbols", BuildTradedSymbolsPage()),
            ("IBKR", BuildLogPage(_logIbkr)),
            ("TEST", BuildLogPage(_logTest)),
            ("WATCHDOG", BuildLogPage(_logWatchdog)),
            ("RUNNER", BuildLogPage(_logRunner)),
            ("IBKR TEST", BuildLogPage(_logIbkrTest)),
            ("IBKR WATCHDOG", BuildLogPage(_logIbkrWatchdog)),
            ("IBKR RUNNER", BuildLogPage(_logIbkrRunner))
        };
        var contentHost = new Panel
        {
            Dock = DockStyle.Fill,
            BackColor = Bg
        };
        for (int i = 0; i < pages.Count; i++)
        {
            var page = pages[i].Content;
            page.Dock = DockStyle.Fill;
            page.Visible = false;
            contentHost.Controls.Add(page);
            ApplyTheme(page);
        }
        void SetPage(int index)
        {
            if (index < 0 || index >= pages.Count) return;
            try
            {
                contentHost.SuspendLayout();
                for (int i = 0; i < pages.Count; i++)
                {
                    pages[i].Content.Visible = (i == index);
                }
                var selected = pages[index].Content;
                selected.BringToFront();
                contentHost.ResumeLayout();
            }
            catch (Exception ex)
            {
                AppendLog(_logLauncher, $"Tab switch error: {ex.Message}");
            }
        }

        var tabNav = BuildTabNav(pages, SetPage);
        var shell = new Panel
        {
            Dock = DockStyle.Fill,
            BackColor = Bg,
            Padding = new Padding(10)
        };
        var frame = new Panel
        {
            Dock = DockStyle.Fill,
            BackColor = Color.FromArgb(18, 21, 27),
            Padding = new Padding(1)
        };
        var host = new Panel
        {
            Dock = DockStyle.Fill,
            BackColor = Bg
        };
        host.Controls.Add(contentHost);
        host.Controls.Add(tabNav);
        frame.Controls.Add(host);
        shell.Controls.Add(frame);
        Controls.Add(shell);
        ApplyTheme(this);
        _busyBar.Controls.Add(_busyPulse);
        _busyBar.Resize += (_, __) =>
        {
            _busyPulse.Height = _busyBar.Height;
        };
        _busyTimer.Tick += (_, __) =>
        {
            if (!_busyBar.Visible) return;
            _busyPulseX += 8;
            if (_busyPulseX > _busyBar.Width)
                _busyPulseX = -_busyPulse.Width;
            _busyPulse.Left = _busyPulseX;
        };
        foreach (var lb in new[] { _logDashboard, _logAutoTune, _logShadowReport, _logControlRuntime, _logLauncher })
        {
            lb.HandleCreated += (_, __) => TryApplyDarkScrollbars(lb);
            if (lb.IsHandleCreated) TryApplyDarkScrollbars(lb);
        }
        _logTradedSymbols.HandleCreated += (_, __) => TryApplyDarkScrollbars(_logTradedSymbols);
        if (_logTradedSymbols.IsHandleCreated) TryApplyDarkScrollbars(_logTradedSymbols);
        SetPage(0);
        Shown += async (_, __) =>
        {
            TryEnableDarkTitleBar();
            await EnsureProjectAutostartOnLaunchAsync();
        };
        HandleCreated += (_, __) => TryEnableDarkTitleBar();

        _healthTimer.Tick += async (_, __) =>
        {
            if (_healthRefreshInProgress) return;
            try
            {
                _healthRefreshInProgress = true;
                await RefreshHealthAsync();
            }
            catch (Exception ex)
            {
                AppendLog(_logLauncher, $"Health refresh error: {ex.Message}");
            }
            finally
            {
                _healthRefreshInProgress = false;
            }
        };
        _healthTimer.Start();
        _watchdogAutostartTimer.Tick += async (_, __) =>
        {
            _watchdogAutostartTimer.Stop();
            if (IsProjectActionLocked())
            {
                AppendLog(_logLauncher, "Watchdog auto-start deferred: project action still in progress.");
                ScheduleWatchdogAutostart(DeferredWatchdogAutostartDelayMs);
                return;
            }
            try
            {
                await EnsureWatchdogAutostartAsync();
            }
            catch (Exception ex)
            {
                AppendLog(_logLauncher, $"Watchdog auto-start failed: {ex.Message}");
            }
        };
        ScheduleWatchdogAutostart(DefaultWatchdogAutostartDelayMs);
        _ = InitWebAsync();

        FormClosing += (_, __) =>
        {
            WriteFileLog("=== Launcher FormClosing ===");
            try { SystemEvents.SessionSwitch -= OnSessionSwitch; } catch { }
            try { _healthTimer.Stop(); } catch { }
            try { _watchdogAutostartTimer.Stop(); } catch { }
            StopTrackedProcesses();
            _http.Dispose();
        };
        Resize += (_, __) => { };
        SizeChanged += (_, __) =>
        {
            _isPseudoMaximized = (WindowState == FormWindowState.Maximized);
        };
        Move += (_, __) => { };
        try { SystemEvents.SessionSwitch += OnSessionSwitch; } catch { }
        WriteLlmProfileMode("WORK");

        // Runtime console should feel like a raw terminal output.
        _logControlRuntime.BackColor = Color.FromArgb(12, 12, 12);
        _logControlRuntime.ForeColor = Color.FromArgb(212, 212, 212);
        _logControlRuntime.Font = new Font("Consolas", 9.5f);
        _logIbkr.BackColor = _logControlRuntime.BackColor;
        _logIbkr.ForeColor = _logControlRuntime.ForeColor;
        _logIbkr.Font = _logControlRuntime.Font;
        _logTest.BackColor = _logControlRuntime.BackColor;
        _logTest.ForeColor = _logControlRuntime.ForeColor;
        _logTest.Font = _logControlRuntime.Font;
        _logWatchdog.BackColor = _logControlRuntime.BackColor;
        _logWatchdog.ForeColor = _logControlRuntime.ForeColor;
        _logWatchdog.Font = _logControlRuntime.Font;
        _logRunner.BackColor = _logControlRuntime.BackColor;
        _logRunner.ForeColor = _logControlRuntime.ForeColor;
        _logRunner.Font = _logControlRuntime.Font;
        _logIbkrTest.BackColor = _logControlRuntime.BackColor;
        _logIbkrTest.ForeColor = _logControlRuntime.ForeColor;
        _logIbkrTest.Font = _logControlRuntime.Font;
        _logIbkrWatchdog.BackColor = _logControlRuntime.BackColor;
        _logIbkrWatchdog.ForeColor = _logControlRuntime.ForeColor;
        _logIbkrWatchdog.Font = _logControlRuntime.Font;
        _logIbkrRunner.BackColor = _logControlRuntime.BackColor;
        _logIbkrRunner.ForeColor = _logControlRuntime.ForeColor;
        _logIbkrRunner.Font = _logControlRuntime.Font;
    }

    private void OnSessionSwitch(object? sender, SessionSwitchEventArgs e)
    {
        if (e.Reason == SessionSwitchReason.SessionLock)
        {
            WriteLlmProfileMode("PERF");
        }
        if (e.Reason == SessionSwitchReason.SessionUnlock)
        {
            WriteFileLog("=== Launcher SessionUnlock ===");
            WriteLlmProfileMode("WORK");
            try
            {
                BeginInvoke(new Action(async () =>
                {
                    try
                    {
                        Show();
                        BringToFront();
                        Activate();
                        if (EmbeddedDashboardEnabled && _web.CoreWebView2 != null)
                        {
                            _web.CoreWebView2.Navigate(DashboardUrl());
                            AppendLog(_logLauncher, "Session unlock recovery: dashboard navigate");
                        }
                        TriggerIbkrGatewayRecoveryFromUnlock();
                        await RefreshHealthAsync();
                    }
                    catch (Exception ex)
                    {
                        AppendLog(_logLauncher, $"Session unlock recovery failed: {ex.Message}");
                    }
                }));
            }
            catch (Exception ex)
            {
                WriteFileLog($"=== SessionUnlock hook invoke failed === {ex.Message}");
            }
        }
    }

    private void WriteLlmProfileMode(string mode)
    {
        try
        {
            if (string.IsNullOrWhiteSpace(RepoRoot()))
                return;
            var profilePath = Path.Combine(PythonCoreDirPath(), "llm_profile_mode.txt");
            File.WriteAllText(profilePath, mode + Environment.NewLine);
            AppendLog(_logLauncher, $"LLM profile mode set: {mode}");
        }
        catch (Exception ex)
        {
            AppendLog(_logLauncher, $"LLM profile mode write failed: {ex.Message}");
        }
    }

    private void HookLifecycleLogging()
    {
        try
        {
            AppDomain.CurrentDomain.ProcessExit += (_, __) =>
            {
                WriteFileLog("=== Launcher ProcessExit ===");
            };
            AppDomain.CurrentDomain.UnhandledException += (_, e) =>
            {
                try
                {
                    WriteFileLog($"=== Launcher UnhandledException === {e.ExceptionObject}");
                }
                catch { }
            };
            Application.ApplicationExit += (_, __) =>
            {
                WriteFileLog("=== Launcher ApplicationExit ===");
            };
            Application.ThreadException += (_, e) =>
            {
                WriteFileLog($"=== Launcher ThreadException === {e.Exception}");
            };
        }
        catch
        {
            // Best effort only.
        }
    }

    private void UpdateWindowRegion()
    {
        if (FormBorderStyle != FormBorderStyle.None)
        {
            Region = null;
            return;
        }
        try
        {
            if (WindowState == FormWindowState.Maximized || _isPseudoMaximized)
            {
                Region = null;
                return;
            }
            var rect = new Rectangle(0, 0, Width, Height);
            using var path = RoundedRect(rect, 12);
            Region = new Region(path);
        }
        catch
        {
            // Ignore cosmetic region update issues.
        }
    }

    private void ApplyMaximizedBounds()
    {
        try
        {
            var wa = Screen.FromHandle(Handle).WorkingArea;
            MaximizedBounds = wa;
        }
        catch
        {
            // Ignore monitor/work-area probing failures.
        }
    }

    private void TogglePseudoMaximize()
    {
        if (FormBorderStyle != FormBorderStyle.None)
        {
            WindowState = (WindowState == FormWindowState.Maximized) ? FormWindowState.Normal : FormWindowState.Maximized;
            return;
        }
        try
        {
            if (WindowState == FormWindowState.Minimized)
                WindowState = FormWindowState.Normal;

            if (_isPseudoMaximized)
            {
                _isPseudoMaximized = false;
                Bounds = _restoreBounds;
            }
            else
            {
                _restoreBounds = Bounds;
                var wa = Screen.FromHandle(Handle).WorkingArea;
                Bounds = wa;
                _isPseudoMaximized = true;
            }
            Show();
            BringToFront();
            Activate();
            UpdateWindowRegion();
        }
        catch
        {
        }
    }

    private static RichTextBox NewLogBox() => new()
    {
        Dock = DockStyle.Fill,
        ReadOnly = true,
        Font = new Font("Consolas", 9f),
        BackColor = Color.FromArgb(18, 21, 27),
        ForeColor = Color.FromArgb(203, 213, 225),
        BorderStyle = BorderStyle.None
    };

    private static void TryApplyDarkScrollbars(Control c)
    {
        try
        {
            _ = SetWindowTheme(c.Handle, "DarkMode_Explorer", null!);
        }
        catch
        {
            try
            {
                _ = SetWindowTheme(c.Handle, "Explorer", null!);
            }
            catch
            {
                // Best-effort only.
            }
        }
    }

    private static BadgeLabel NewBadge() => new()
    {
        AutoSize = false,
        Width = 320,
        Height = 28,
        Padding = new Padding(10, 6, 10, 6),
        Margin = new Padding(0, 0, 6, 6),
        ForeColor = Color.FromArgb(187, 247, 208),
        BackColor = Color.FromArgb(5, 46, 22),
        BorderColor = Color.FromArgb(22, 163, 74)
    };

    private Control BuildControlPage()
    {
        var page = new Panel { BackColor = Bg, ForeColor = Fg, Dock = DockStyle.Fill };
        var rootRow = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 38, AutoSize = false, Padding = new Padding(8), BackColor = Color.Transparent };
        rootRow.Controls.Add(new Label { Text = "Project Root:", AutoSize = true, TextAlign = ContentAlignment.MiddleLeft, Padding = new Padding(0, 7, 0, 0) });
        rootRow.Controls.Add(WrapInput(_txtRoot));

        var portsRow = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 42, AutoSize = false, Padding = new Padding(8), BackColor = Color.Transparent };
        portsRow.Controls.Add(new Label { Text = "API:", AutoSize = true, Padding = new Padding(0, 7, 0, 0) });
        portsRow.Controls.Add(WrapInput(_txtApiPort));
        portsRow.Controls.Add(new Label { Text = "Backtest:", AutoSize = true, Padding = new Padding(8, 7, 0, 0) });
        portsRow.Controls.Add(WrapInput(_txtBacktestPort));
        portsRow.Controls.Add(new Label { Text = "Dashboard:", AutoSize = true, Padding = new Padding(8, 7, 0, 0) });
        portsRow.Controls.Add(WrapInput(_txtDashPort));
        portsRow.Controls.Add(_chkAutoStartBot);

        var btnRow = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 48, AutoSize = false, Padding = new Padding(8), BackColor = Color.Transparent };
        var btnStopProject = new Button { Text = "Stop Project", Width = 120 };
        var btnStartBot = new Button { Text = "Start Bot", Width = 100 };
        var btnStopBot = new Button { Text = "Stop Bot", Width = 100 };
        var btnStartApi = new Button { Text = "Start API", Width = 100 };
        var btnStopApi = new Button { Text = "Stop API", Width = 100 };
        var btnOpen = new Button { Text = "Open Dashboard", Width = 130 };
        var btnPorts = new Button { Text = "Check Ports", Width = 110 };
        StyleButton(_btnProjectAction, Color.FromArgb(22, 163, 74));
        StyleButton(btnStopProject, Color.FromArgb(220, 38, 38));
        StyleButton(btnStartBot, Color.FromArgb(2, 132, 199));
        StyleButton(btnStopBot, Color.FromArgb(217, 119, 6));
        StyleButton(btnStartApi, Color.FromArgb(2, 132, 199));
        StyleButton(btnStopApi, Color.FromArgb(217, 119, 6));
        StyleButton(btnOpen, Color.FromArgb(79, 70, 229));
        StyleButton(btnPorts, Color.FromArgb(71, 85, 105));
        _btnProjectAction.Click += async (_, __) => await SmartStartProjectAsync();
        btnStopProject.Click += async (_, __) => await StopProjectFromUiAsync();
        btnStartBot.Click += async (_, __) => await StartBotAsync();
        btnStopBot.Click += async (_, __) => await StopBotAsync();
        btnStartApi.Click += async (_, __) => await StartApiAsync();
        btnStopApi.Click += async (_, __) => await StopApiAsync();
        btnOpen.Click += (_, __) => OpenDashboardExternal();
        btnPorts.Click += async (_, __) => await RefreshPortDetailsAsync(logResult: true);
        btnRow.Controls.AddRange(new Control[] { _btnProjectAction, btnStopProject, btnStartBot, btnStopBot, btnStartApi, btnStopApi, btnOpen, btnPorts });

        var controlCard = new CardPanel { Dock = DockStyle.Top, Height = 136, Padding = new Padding(10) };
        controlCard.Controls.Add(btnRow);
        controlCard.Controls.Add(portsRow);
        controlCard.Controls.Add(rootRow);

        var status = new CardPanel { Dock = DockStyle.Top, Height = 240, Padding = new Padding(12) };
        var statGrid = new TableLayoutPanel
        {
            Dock = DockStyle.Top,
            AutoSize = true,
            AutoSizeMode = AutoSizeMode.GrowAndShrink,
            ColumnCount = 2,
            RowCount = 5,
            BackColor = Color.Transparent,
            Padding = new Padding(0, 2, 0, 0),
        };
        _statusGrid = statGrid;

        _lblApi.Dock = DockStyle.Fill;
        _lblBot.Dock = DockStyle.Fill;
        _lblDash.Dock = DockStyle.Fill;
        _lblApiPort.Dock = DockStyle.Fill;
        _lblBacktestPort.Dock = DockStyle.Fill;
        _lblDashPort.Dock = DockStyle.Fill;
        _lblCollector.Dock = DockStyle.Fill;
        _lblNews.Dock = DockStyle.Fill;
        _lblMarketData.Dock = DockStyle.Fill;
        _lblMarketIntel.Dock = DockStyle.Fill;
        _lblIbkr.Dock = DockStyle.Fill;

        _statusBadges.Clear();
        _statusBadges.AddRange(new[]
        {
            _lblApi, _lblBot, _lblDash, _lblApiPort, _lblBacktestPort,
            _lblDashPort, _lblCollector, _lblNews, _lblMarketData, _lblMarketIntel
        });
        SetBadge(_lblApi, "API ...", BadgeTone.Neutral);
        SetBadge(_lblBot, "Bot ...", BadgeTone.Neutral);
        SetBadge(_lblDash, "Dashboard ...", BadgeTone.Neutral);
        SetBadge(_lblApiPort, "API port ...", BadgeTone.Neutral);
        SetBadge(_lblBacktestPort, "Backtest port ...", BadgeTone.Neutral);
        SetBadge(_lblDashPort, "Dashboard port ...", BadgeTone.Neutral);
        SetBadge(_lblCollector, "Collector ...", BadgeTone.Neutral);
        SetBadge(_lblNews, "News worker ...", BadgeTone.Neutral);
        SetBadge(_lblMarketData, "Market data worker ...", BadgeTone.Neutral);
        SetBadge(_lblMarketIntel, "Market intel worker ...", BadgeTone.Neutral);
        SetBadge(_lblIbkr, "IBKR ...", BadgeTone.Neutral);
        SetBadge(_lblTest, "TEST ...", BadgeTone.Neutral);
        SetBadge(_lblWatchdog, "WATCHDOG ...", BadgeTone.Neutral);
        ReflowStatusGrid();
        status.Controls.Add(statGrid);
        var spacer = new Panel { Dock = DockStyle.Top, Height = 16, BackColor = Bg };

        var runtimeCard = new CardPanel { Dock = DockStyle.Fill, Padding = new Padding(10) };
        var runtimeTitle = new Label
        {
            Text = "Runtime Console",
            Dock = DockStyle.Top,
            Height = 20,
            ForeColor = Color.FromArgb(148, 163, 184),
        };
        _logControlRuntime.Dock = DockStyle.Fill;
        runtimeCard.Controls.Add(_logControlRuntime);
        runtimeCard.Controls.Add(runtimeTitle);

        page.Controls.Add(runtimeCard);
        page.Controls.Add(status);
        page.Controls.Add(spacer);
        page.Controls.Add(_busyBar);
        page.Controls.Add(controlCard);
        page.Resize += (_, __) => ReflowStatusGrid();
        return page;
    }

    private void ReflowStatusGrid()
    {
        if (_statusGrid == null || _statusBadges.Count == 0)
            return;

        int width = _statusGrid.Parent?.ClientSize.Width ?? _statusGrid.ClientSize.Width;
        int cols = width switch
        {
            >= 1200 => 3,
            >= 760 => 2,
            _ => 1
        };
        cols = Math.Max(1, cols);
        int rows = (int)Math.Ceiling(_statusBadges.Count / (double)cols);

        _statusGrid.SuspendLayout();
        _statusGrid.Controls.Clear();
        _statusGrid.ColumnStyles.Clear();
        _statusGrid.RowStyles.Clear();
        _statusGrid.ColumnCount = cols;
        _statusGrid.RowCount = rows;
        for (int c = 0; c < cols; c++) _statusGrid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f / cols));
        for (int r = 0; r < rows; r++) _statusGrid.RowStyles.Add(new RowStyle(SizeType.AutoSize));

        for (int i = 0; i < _statusBadges.Count; i++)
        {
            int r = i / cols;
            int c = i % cols;
            _statusGrid.Controls.Add(_statusBadges[i], c, r);
        }
        _statusGrid.ResumeLayout();
    }

    private static Control WrapInput(TextBox tb)
    {
        var host = new InputHostPanel
        {
            Width = tb.Width + 14,
            Height = 26,
            Margin = new Padding(0, 2, 0, 0),
            Padding = new Padding(8, 5, 8, 5)
        };
        tb.BorderStyle = BorderStyle.None;
        tb.Margin = new Padding(0);
        tb.Dock = DockStyle.Fill;
        host.Controls.Add(tb);
        return host;
    }

    private Control BuildDashboardPage()
    {
        var page = new Panel { BackColor = Bg, ForeColor = Fg, Dock = DockStyle.Fill };
        if (EmbeddedDashboardEnabled)
        {
            page.Controls.Add(_web);
            return page;
        }

        var card = new CardPanel { Dock = DockStyle.Top, Height = 132, Padding = new Padding(16), Margin = new Padding(16) };
        var title = new Label
        {
            Text = "Embedded dashboard is disabled in launcher.",
            AutoSize = true,
            ForeColor = Fg,
            Font = new Font("Segoe UI", 11f, FontStyle.Bold),
            Location = new Point(16, 18)
        };
        var desc = new Label
        {
            Text = "API, tests, data collection and dashboard process keep running. Use 'Open Dashboard' for the external UI.",
            AutoSize = true,
            ForeColor = Muted,
            Location = new Point(16, 50)
        };
        var btn = new Button
        {
            Text = "Open Dashboard",
            Width = 150,
            Height = 34,
            Location = new Point(16, 78)
        };
        StyleButton(btn, Color.FromArgb(79, 70, 229));
        btn.Click += (_, __) => OpenDashboardExternal();
        card.Controls.Add(title);
        card.Controls.Add(desc);
        card.Controls.Add(btn);
        page.Controls.Add(card);
        return page;
    }

    private static Control BuildLogPage(Control content)
    {
        var p = new Panel { BackColor = Bg, ForeColor = Fg, Dock = DockStyle.Fill };
        p.Controls.Add(content);
        return p;
    }

    private Control BuildShadowReportPage()
    {
        var page = new Panel { BackColor = Bg, ForeColor = Fg, Dock = DockStyle.Fill };
        var top = new Panel { Dock = DockStyle.Top, Height = 46, Padding = new Padding(8, 8, 8, 6), BackColor = Bg };
        var lbl = new Label
        {
            AutoSize = true,
            Text = "Actions scope:",
            ForeColor = Muted,
            BackColor = Color.Transparent,
            Margin = new Padding(0, 7, 8, 0)
        };
        _cmbShadowActions.Items.Clear();
        _cmbShadowActions.Items.Add("Shadow only");
        _cmbShadowActions.Items.Add("Shadow + Policy + Executed");
        _cmbShadowActions.SelectedIndex = 0;
        _cmbShadowActions.BackColor = InputFill;
        _cmbShadowActions.ForeColor = Fg;
        _cmbShadowActions.FlatStyle = FlatStyle.Flat;
        _cmbShadowActions.SelectedIndexChanged += async (_, __) =>
        {
            _shadowActionsParam = _cmbShadowActions.SelectedIndex == 1
                ? "shadow,policy,executed"
                : "shadow";
            _lastShadowReportPayload = null;
            try { await RefreshHealthAsync(); } catch { }
        };

        var btnRefresh = new Button
        {
            Text = "Refresh",
            Width = 96,
            Height = 30,
            Margin = new Padding(8, 0, 0, 0)
        };
        StyleButton(btnRefresh, Color.FromArgb(71, 85, 105));
        btnRefresh.Click += async (_, __) =>
        {
            _lastShadowReportPayload = null;
            try { await RefreshHealthAsync(); } catch { }
        };

        var flow = new FlowLayoutPanel
        {
            Dock = DockStyle.Fill,
            BackColor = Color.Transparent,
            FlowDirection = FlowDirection.LeftToRight,
            WrapContents = false,
            AutoSize = false
        };
        flow.Controls.Add(lbl);
        flow.Controls.Add(_cmbShadowActions);
        flow.Controls.Add(btnRefresh);
        top.Controls.Add(flow);

        page.Controls.Add(_logShadowReport);
        page.Controls.Add(top);
        return page;
    }

    private Control BuildTradedSymbolsPage()
    {
        var page = new Panel { BackColor = Bg, ForeColor = Fg, Dock = DockStyle.Fill };
        var top = new Panel { Dock = DockStyle.Top, Height = 46, Padding = new Padding(8, 8, 8, 6), BackColor = Bg };
        var lbl = new Label
        {
            AutoSize = true,
            Text = "Unique symbols from closed trades + paper + shadow/policy/executed signals across recent runs:",
            ForeColor = Muted,
            BackColor = Color.Transparent,
            Margin = new Padding(0, 7, 8, 0)
        };
        var btnRefresh = new Button
        {
            Text = "Refresh",
            Width = 96,
            Height = 30,
            Margin = new Padding(8, 0, 0, 0)
        };
        StyleButton(btnRefresh, Color.FromArgb(71, 85, 105));
        btnRefresh.Click += async (_, __) =>
        {
            _lastTradedSymbolsFetchUtc = DateTime.MinValue;
            try { await UpdateTradedSymbolsLogAsync(force: true); } catch { }
        };

        var flow = new FlowLayoutPanel
        {
            Dock = DockStyle.Fill,
            BackColor = Color.Transparent,
            FlowDirection = FlowDirection.LeftToRight,
            WrapContents = false,
            AutoSize = false
        };
        flow.Controls.Add(lbl);
        flow.Controls.Add(btnRefresh);
        top.Controls.Add(flow);

        _logTradedSymbols.WordWrap = false;
        _logTradedSymbols.Text = "Loading traded symbols...";
        page.Controls.Add(_logTradedSymbols);
        page.Controls.Add(top);
        return page;
    }

    private async Task InitWebAsync()
    {
        if (!EmbeddedDashboardEnabled)
            return;
        try
        {
            await _web.EnsureCoreWebView2Async();
            _web.Source = new Uri(DashboardUrl());
        }
        catch (Exception ex)
        {
            AppendLog(_logLauncher, $"WebView2 init failed: {ex.Message}");
        }
    }

    private string DashboardUrl() => $"http://127.0.0.1:{_txtDashPort.Text.Trim()}";
    private string ApiBase() => $"http://127.0.0.1:{_txtApiPort.Text.Trim()}";
    private string BacktestBase() => $"http://127.0.0.1:{_txtBacktestPort.Text.Trim()}";
    private string RepoRoot() => _txtRoot.Text.Trim();
    private string LayoutConfigPath() => Path.Combine(RepoRoot(), "aiinvest.layout.json");
    private string ResolveLayoutDir(string propertyName, string fallbackDirName)
    {
        try
        {
            var configPath = LayoutConfigPath();
            if (File.Exists(configPath))
            {
                using var doc = JsonDocument.Parse(File.ReadAllText(configPath));
                if (doc.RootElement.TryGetProperty(propertyName, out var prop))
                {
                    var dirName = prop.GetString();
                    if (!string.IsNullOrWhiteSpace(dirName))
                    {
                        var resolved = Path.Combine(RepoRoot(), dirName);
                        if (Directory.Exists(resolved))
                            return resolved;
                    }
                }
            }
        }
        catch
        {
        }
        var fallback = Path.Combine(RepoRoot(), fallbackDirName);
        return Directory.Exists(fallback) ? fallback : RepoRoot();
    }
    private string ProjectDirPath()
    {
        return ResolveLayoutDir("project_dir", "PRJCT");
    }
    private string DatabaseRootDir()
    {
        return ResolveLayoutDir("database_dir", "DTB");
    }
    private string ReportsRootDir()
    {
        return ResolveLayoutDir("reports_dir", "RPRTS");
    }
    private string PythonCoreDirPath() => Path.Combine(ProjectDirPath(), "python-core");
    private string DashboardDirPath() => Path.Combine(ProjectDirPath(), "dashboard");
    private string MongoDaemonPath() => Path.Combine(DatabaseRootDir(), "MongoDB", "server", "6.0", "bin", "mongod.exe");
    private string MongoDataDirPath() => Path.Combine(DatabaseRootDir(), "MongoDB", "data");
    private string ProjectScriptPath(string scriptName) => Path.Combine(ProjectDirPath(), scriptName);

    private static bool IsIbkrGatewayRunning()
    {
        try
        {
            foreach (var proc in Process.GetProcesses())
            {
                try
                {
                    var name = proc.ProcessName ?? string.Empty;
                    if (name.IndexOf("ibgateway", StringComparison.OrdinalIgnoreCase) >= 0 ||
                        name.IndexOf("tws", StringComparison.OrdinalIgnoreCase) >= 0)
                        return true;
                }
                catch
                {
                }
                finally
                {
                    proc.Dispose();
                }
            }
        }
        catch
        {
        }
        return false;
    }

    private Dictionary<string, string> ReadLocalEnvFile()
    {
        var values = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        try
        {
            var envPath = Path.Combine(PythonCoreDirPath(), ".env");
            if (!File.Exists(envPath))
                return values;

            foreach (var line in File.ReadLines(envPath))
            {
                var trimmed = (line ?? string.Empty).Trim();
                if (string.IsNullOrWhiteSpace(trimmed) || trimmed.StartsWith("#", StringComparison.Ordinal) || !trimmed.Contains('='))
                    continue;

                var parts = trimmed.Split('=', 2);
                if (parts.Length != 2)
                    continue;

                var key = parts[0].Trim();
                var value = parts[1].Trim().Trim('\'', '"');
                if (!string.IsNullOrWhiteSpace(key))
                    values[key] = value;
            }
        }
        catch
        {
        }
        return values;
    }

    private static string GetEnvValue(IReadOnlyDictionary<string, string> envVars, params string[] names)
    {
        foreach (var name in names)
        {
            if (envVars.TryGetValue(name, out var value) && !string.IsNullOrWhiteSpace(value))
                return value.Trim();
        }
        return string.Empty;
    }

    private static string NormalizeIbkrGatewayTradingMode(string? mode)
    {
        var raw = (mode ?? string.Empty).Trim().ToLowerInvariant();
        return raw switch
        {
            "live" => "live",
            "l" => "live",
            _ => "paper"
        };
    }

    private static bool IsLocalIbkrHost(string? host)
    {
        var raw = (host ?? "127.0.0.1").Trim().ToLowerInvariant();
        return raw is "127.0.0.1" or "localhost" or "::1";
    }

    private static List<int> GetIbkrPortCandidates(int configuredPort, string tradingMode)
    {
        var preferred = string.Equals(tradingMode, "live", StringComparison.OrdinalIgnoreCase)
            ? new[] { 4001, 7496 }
            : new[] { 4002, 7497 };
        var fallback = string.Equals(tradingMode, "live", StringComparison.OrdinalIgnoreCase)
            ? new[] { 4002, 7497 }
            : new[] { 4001, 7496 };
        var ports = new List<int>();
        foreach (var port in new[] { configuredPort }.Concat(preferred).Concat(fallback))
        {
            if (port <= 0 || ports.Contains(port))
                continue;
            ports.Add(port);
        }
        if (ports.Count == 0)
            ports.Add(string.Equals(tradingMode, "live", StringComparison.OrdinalIgnoreCase) ? 4001 : 4002);
        return ports;
    }

    private int GetConfiguredIbkrPort(IReadOnlyDictionary<string, string> envVars, string tradingMode)
    {
        var fallback = string.Equals(tradingMode, "live", StringComparison.OrdinalIgnoreCase) ? 4001 : 4002;
        var raw = GetEnvValue(envVars, "IBKR_TWS_PORT");
        return int.TryParse(raw, out var port) && port > 0 ? port : fallback;
    }

    private static string GetConfiguredIbkrHost(IReadOnlyDictionary<string, string> envVars)
    {
        var host = GetEnvValue(envVars, "IBKR_TWS_HOST");
        return string.IsNullOrWhiteSpace(host) ? "127.0.0.1" : host;
    }

    private static string GetConfiguredIbkrTradingMode(IReadOnlyDictionary<string, string> envVars)
    {
        return NormalizeIbkrGatewayTradingMode(GetEnvValue(envVars, "IBKR_GATEWAY_TRADING_MODE", "IBKR_TRADING_MODE"));
    }

    private static bool HasIbkrGatewayCredentials(IReadOnlyDictionary<string, string> envVars)
    {
        var username = GetEnvValue(envVars, "IBKR_GATEWAY_USERNAME", "IBKR_USERNAME", "USERNAME");
        var password = GetEnvValue(envVars, "IBKR_GATEWAY_PASSWORD", "IBKR_PASSWORD", "PASSWORD");
        return !string.IsNullOrWhiteSpace(username) && !string.IsNullOrWhiteSpace(password);
    }

    private int? FindListeningIbkrPort(string host, int configuredPort, string tradingMode)
    {
        if (!IsLocalIbkrHost(host))
            return IsPortListening(configuredPort) ? configuredPort : null;

        foreach (var port in GetIbkrPortCandidates(configuredPort, tradingMode))
        {
            if (IsPortListening(port))
                return port;
        }
        return null;
    }

    private void MaybeRecoverIbkrGatewayAsync(
        IReadOnlyDictionary<string, string> envVars,
        string host,
        int configuredPort,
        string tradingMode,
        bool ibkrPortOk,
        bool ibkrProcOk)
    {
        if (ibkrPortOk)
            return;
        var reason = ibkrProcOk ? "API port is down" : "gateway process is not running";
        RequestIbkrGatewayRecovery(envVars, host, configuredPort, tradingMode, reason, forceCooldownBypass: false);
    }

    private void TriggerIbkrGatewayRecoveryFromUnlock()
    {
        try
        {
            var envVars = ReadLocalEnvFile();
            var host = GetConfiguredIbkrHost(envVars);
            var tradingMode = GetConfiguredIbkrTradingMode(envVars);
            var configuredPort = GetConfiguredIbkrPort(envVars, tradingMode);
            var ibkrPortOk = FindListeningIbkrPort(host, configuredPort, tradingMode).HasValue;
            if (ibkrPortOk)
            {
                AppendLog(_logLauncher, $"Session unlock recovery: IBKR API already reachable | host {host} | port {configuredPort} | mode {tradingMode}");
                return;
            }

            AppendLog(_logLauncher, $"Session unlock recovery: requesting IBKR Gateway ensure | host {host} | port {configuredPort} | mode {tradingMode}");
            RequestIbkrGatewayRecovery(envVars, host, configuredPort, tradingMode, "session unlock", forceCooldownBypass: true);
        }
        catch (Exception ex)
        {
            AppendLog(_logLauncher, $"Session unlock IBKR recovery failed: {ex.Message}");
        }
    }

    private void RequestIbkrGatewayRecovery(
        IReadOnlyDictionary<string, string> envVars,
        string host,
        int configuredPort,
        string tradingMode,
        string reason,
        bool forceCooldownBypass)
    {
        if (!IsLocalIbkrHost(host) || IsProjectActionLocked())
            return;
        if (Interlocked.CompareExchange(ref _ibkrGatewayEnsureInProgress, 0, 0) == 1)
            return;
        if (!forceCooldownBypass &&
            (DateTime.UtcNow - _lastIbkrGatewayEnsureUtc).TotalSeconds < IbkrGatewayRecoveryCooldownSeconds)
            return;
        if (!HasIbkrGatewayCredentials(envVars))
            return;
        if (Interlocked.Exchange(ref _ibkrGatewayEnsureInProgress, 1) == 1)
            return;

        _lastIbkrGatewayEnsureUtc = DateTime.UtcNow;
        AppendLog(_logLauncher, $"IBKR Gateway recovery requested: {reason} | host {host} | port {configuredPort} | mode {tradingMode}");
        _ = RecoverIbkrGatewayAsync(host, configuredPort, tradingMode);
    }

    private async Task RecoverIbkrGatewayAsync(string host, int configuredPort, string tradingMode)
    {
        try
        {
            var root = RepoRoot();
            var scriptPath = ProjectScriptPath("ensure_ibkr_gateway.ps1");
            if (!File.Exists(scriptPath))
            {
                AppendLog(_logLauncher, $"IBKR Gateway recovery skipped: missing script {scriptPath}");
                return;
            }

            var proc = StartProcess(
                "powershell.exe",
                $"-Sta -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"{scriptPath}\" -ProjectRoot \"{root}\" -LoginTimeoutSec 150",
                root,
                "IBKR-GATEWAY",
                _logIbkr
            );
            await proc.WaitForExitAsync();
            AppendLog(
                _logLauncher,
                proc.ExitCode == 0
                    ? $"IBKR Gateway recovery finished successfully | host {host} | port {configuredPort} | mode {tradingMode}"
                    : $"IBKR Gateway recovery failed with exit code {proc.ExitCode} | host {host} | port {configuredPort} | mode {tradingMode}");
        }
        catch (Exception ex)
        {
            AppendLog(_logLauncher, $"IBKR Gateway recovery failed: {ex.Message}");
        }
        finally
        {
            Volatile.Write(ref _ibkrGatewayEnsureInProgress, 0);
        }
    }

    private async Task StartProjectAsync(long actionId, bool autoStartBot = true)
    {
        try
        {
            SetBusy(true, "Starting project...");
            NormalizeProcessRefs();

            var root = RepoRoot();
            var py = Path.Combine(PythonCoreDirPath(), "venv", "Scripts", "python.exe");
            var dashDir = DashboardDirPath();
            var coreDir = PythonCoreDirPath();
            var npm = @"C:\Program Files\nodejs\npm.cmd";
            var mongoOk = await EnsureMongoDbAsync();

            if (!File.Exists(py))
            {
                AppendLog(_logLauncher, $"Missing python venv: {py}");
                return;
            }
            if (!mongoOk)
                AppendLog(_logLauncher, "MongoDB is still unavailable after start attempt. Collector may fail.");

            var apiPortText = _txtApiPort.Text.Trim();
            var backtestPortText = _txtBacktestPort.Text.Trim();
            var dashPortText = _txtDashPort.Text.Trim();
            var apiHealthy = await IsStrictOkAsync($"{ApiBase()}/health");
            var backtestHealthy = await IsStrictOkAsync($"{BacktestBase()}/health");
            var dashHealthy = await IsStrictOkAsync(DashboardUrl(), timeoutSec: 5);

            async Task<bool> PrepareComponentPortAsync(string label, string portText, bool healthy)
            {
                if (!int.TryParse(portText, out var pnum)) return false;
                if (!IsPortListening(pnum)) return false;

                if (healthy)
                {
                    AppendLog(_logLauncher, $"{label} already healthy on port {pnum}. Reusing existing process.");
                    return true;
                }

                AppendLog(_logLauncher, $"{label} port {pnum} is occupied but unhealthy. Restarting process.");
                StopByListeningPort(portText);
                await Task.Delay(500);
                if (!IsPortListening(pnum)) return false;

                AppendLog(_logLauncher, $"Cannot start {label}: port {pnum} is still occupied.");
                throw new InvalidOperationException($"{label} port {pnum} remains occupied.");
            }

            var reuseApi = await PrepareComponentPortAsync("API", apiPortText, apiHealthy);
            var reuseBacktest = await PrepareComponentPortAsync("BACKTEST", backtestPortText, backtestHealthy);
            var reuseDashboard = await PrepareComponentPortAsync("DASHBOARD", dashPortText, dashHealthy);

            NormalizeProcessRefs();
            if (!reuseApi)
                _apiProc = null;
            if (!reuseBacktest)
                _backtestProc = null;
            if (!reuseDashboard)
                _dashboardProc = null;

            _apiProc ??= StartProcess(py, $"-m uvicorn app:app --host 127.0.0.1 --port {apiPortText}", coreDir, "API");
            _backtestProc ??= StartProcess(py, $"-m uvicorn app:app --host 127.0.0.1 --port {backtestPortText}", coreDir, "BACKTEST");
            _collectorProc ??= StartProcess(py, "data_collector.py", coreDir, "COLLECTOR");
            // News worker runs inside API background tasks. Do not start a duplicate standalone process.
            if (!reuseDashboard)
            {
                _dashboardProc ??= StartProcess(
                    "powershell.exe",
                    $"-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command \"$env:VITE_API_BASE='http://127.0.0.1:{_txtApiPort.Text.Trim()}'; $env:VITE_BACKTEST_API_BASE='http://127.0.0.1:{_txtBacktestPort.Text.Trim()}'; & '{npm}' run -s build; if ($LASTEXITCODE -ne 0) {{ exit $LASTEXITCODE }}; & '{npm}' run -s preview -- --host 127.0.0.1 --port {dashPortText} --strictPort\"",
                    dashDir,
                    "DASHBOARD",
                    _logDashboard
                );
            }

            await Task.Delay(1500);
            if (autoStartBot && _chkAutoStartBot.Checked)
                await EnsureBotAutostartAsync(actionId);
            if (EmbeddedDashboardEnabled && _web.CoreWebView2 != null) _web.CoreWebView2.Navigate(DashboardUrl());
            AppendLog(_logLauncher, "Start sequence finished.");
        }
        catch (Exception ex)
        {
            AppendLog(_logLauncher, $"Start failed: {ex.Message}");
        }
        finally
        {
            SetBusy(false, null);
        }
    }

    private async Task SmartStartProjectAsync()
    {
        await RunExclusiveProjectActionAsync(
            "Ignored duplicate project action: another start/stop/restart is already in progress.",
            async actionId =>
            {
                if (IsProjectRunning())
                    await RestartProjectAsync(actionId);
                else
                    await StartProjectAsync(actionId);
            }
        );
    }

    private async Task EnsureProjectAutostartOnLaunchAsync()
    {
        await Task.Delay(500);
        NormalizeProcessRefs();
        AppendLog(_logLauncher, "Launch auto-start ensure requested (bot autostart skipped).");
        await RunExclusiveProjectActionAsync(
            "Ignored duplicate launch auto-start: another start/stop/restart is already in progress.",
            actionId => StartProjectAsync(actionId, autoStartBot: false)
        );
    }

    private async Task StopProjectFromUiAsync()
    {
        await RunExclusiveProjectActionAsync(
            "Ignored duplicate project stop: another start/stop/restart is already in progress.",
            actionId => StopProjectAsync(hard: true, actionId)
        );
    }

    private bool IsProjectActionLocked()
    {
        return Volatile.Read(ref _projectActionInProgress) == 1;
    }

    private bool IsCurrentProjectAction(long actionId)
    {
        return IsProjectActionLocked() && Volatile.Read(ref _projectActionId) == actionId;
    }

    private async Task RunExclusiveProjectActionAsync(string duplicateLogMessage, Func<long, Task> action)
    {
        if (Interlocked.Exchange(ref _projectActionInProgress, 1) == 1)
        {
            AppendLog(_logLauncher, duplicateLogMessage);
            return;
        }

        var actionId = Interlocked.Increment(ref _projectActionId);
        try
        {
            await action(actionId);
        }
        finally
        {
            Volatile.Write(ref _projectActionInProgress, 0);
        }
    }

    private void ScheduleWatchdogAutostart(int delayMs)
    {
        if (IsDisposed || Disposing)
            return;
        if (InvokeRequired)
        {
            try
            {
                BeginInvoke(new Action(() => ScheduleWatchdogAutostart(delayMs)));
            }
            catch
            {
            }
            return;
        }

        _watchdogAutostartTimer.Stop();
        _watchdogAutostartTimer.Interval = Math.Max(1000, delayMs);
        _watchdogAutostartTimer.Start();
    }

    private async Task StartApiAsync()
    {
        try
        {
            if (IsProjectActionLocked())
            {
                AppendLog(_logLauncher, "Ignored manual API start: project start/stop/restart is already in progress.");
                return;
            }
            NormalizeProcessRefs();
            if (IsAlive(_apiProc))
            {
                AppendLog(_logLauncher, "API is already running.");
                return;
            }

            var py = Path.Combine(PythonCoreDirPath(), "venv", "Scripts", "python.exe");
            var coreDir = PythonCoreDirPath();
            if (!File.Exists(py))
            {
                AppendLog(_logLauncher, $"Missing python venv: {py}");
                return;
            }

            if (int.TryParse(_txtApiPort.Text.Trim(), out var apiPort) && IsPortListening(apiPort))
            {
                StopByListeningPort(_txtApiPort.Text.Trim());
                await Task.Delay(500);
            }
            if (int.TryParse(_txtApiPort.Text.Trim(), out apiPort) && IsPortListening(apiPort))
            {
                AppendLog(_logLauncher, $"Start API aborted: port {apiPort} remains occupied by another process.");
                return;
            }

            _apiProc = StartProcess(py, $"-m uvicorn app:app --host 127.0.0.1 --port {_txtApiPort.Text.Trim()}", coreDir, "API");
            AppendLog(_logLauncher, "Start API requested.");
            await Task.Delay(500);
        }
        catch (Exception ex)
        {
            AppendLog(_logLauncher, $"Start API failed: {ex.Message}");
        }
    }

    private async Task StopApiAsync()
    {
        try
        {
            if (IsProjectActionLocked())
            {
                AppendLog(_logLauncher, "Ignored manual API stop: project start/stop/restart is already in progress.");
                return;
            }
            if (_apiProc == null)
            {
                AppendLog(_logLauncher, "API is not running.");
                return;
            }
            KillProcessTree(_apiProc);
            _apiProc = null;
            AppendLog(_logLauncher, "Stop API requested.");
            await Task.Delay(300);
        }
        catch (Exception ex)
        {
            AppendLog(_logLauncher, $"Stop API failed: {ex.Message}");
        }
    }

    private static void KillProcessTree(Process? p)
    {
        if (p == null) return;
        try
        {
            if (!p.HasExited)
                p.Kill(entireProcessTree: true);
        }
        catch
        {
            // best effort
        }
    }

    private async Task StopProjectAsync(bool hard, long actionId)
    {
        try
        {
            SetBusy(true, hard ? "Hard stopping project..." : "Stopping project...");
            await StopBotAsync(skipProjectActionLock: true);
            StopTrackedProcesses();
            if (hard)
            {
                StopExternalProjectProcesses();
                AppendLog(_logLauncher, "Hard stop sequence finished.");
            }
            else
            {
                AppendLog(_logLauncher, "Safe stop sequence finished.");
            }
        }
        finally
        {
            SetBusy(false, null);
        }
    }

    private async Task RestartProjectAsync(long actionId)
    {
        try
        {
            SetBusy(true, "Restarting project...");
            AppendLog(_logLauncher, "Restart sequence started.");
            await StopProjectAsync(hard: true, actionId);
            await EnsurePortsReleasedAsync();
            await Task.Delay(800);
            NormalizeProcessRefs();
            await StartProjectAsync(actionId);
            if (IsCurrentProjectAction(actionId))
            {
                await Task.Delay(500);
                await StartWatchdogAsync();
                AppendLog(_logLauncher, "Restart sequence restored WATCHDOG. TEST and REPORT are managed by watchdog.");
            }
        }
        catch (Exception ex)
        {
            AppendLog(_logLauncher, $"Restart failed: {ex.Message}");
        }
        finally
        {
            SetBusy(false, null);
        }
    }

    private void SetBusy(bool busy, string? message)
    {
        _busy = busy;
        if (_busyBar.InvokeRequired)
        {
            _busyBar.BeginInvoke(new Action(() => SetBusy(busy, message)));
            return;
        }
        _busyBar.Visible = busy;
        if (busy)
        {
            _busyPulseX = -_busyPulse.Width;
            _busyPulse.Left = _busyPulseX;
            _busyTimer.Start();
        }
        else
        {
            _busyTimer.Stop();
        }
        UseWaitCursor = busy;
        if (!string.IsNullOrWhiteSpace(message))
            AppendLog(_logLauncher, message);
    }

    private void NormalizeProcessRefs()
    {
        if (!IsAlive(_apiProc)) _apiProc = null;
        if (!IsAlive(_backtestProc)) _backtestProc = null;
        if (!IsAlive(_collectorProc)) _collectorProc = null;
        if (!IsAlive(_newsProc)) _newsProc = null;
        if (!IsAlive(_marketDataProc)) _marketDataProc = null;
        if (!IsAlive(_marketIntelProc)) _marketIntelProc = null;
        if (!IsAlive(_dashboardProc)) _dashboardProc = null;
        if (!IsAlive(_testProc)) _testProc = null;
        if (!IsAlive(_watchdogProc)) _watchdogProc = null;
        ReattachRuntimeProcesses();
    }

    private void ReattachRuntimeProcesses()
    {
        if (_apiProc == null && int.TryParse(_txtApiPort.Text.Trim(), out var apiPort))
            _apiProc = TryAttachExpectedProcess("main", "api", () => GetPidsFromNetstat(apiPort).FirstOrDefault());
        if (_backtestProc == null && int.TryParse(_txtBacktestPort.Text.Trim(), out var backtestPort))
            _backtestProc = TryAttachExpectedProcess("main", "backtest", () => GetPidsFromNetstat(backtestPort).FirstOrDefault());
        if (_collectorProc == null)
            _collectorProc = TryAttachExpectedProcess("main", "collector", FindCollectorProcessId);
        if (_dashboardProc == null && int.TryParse(_txtDashPort.Text.Trim(), out var dashPort))
            _dashboardProc = TryAttachExpectedProcess("main", "dashboard", () => GetPidsFromNetstat(dashPort).FirstOrDefault());
    }

    private async Task EnsurePortsReleasedAsync()
    {
        var ports = new[] { _txtApiPort.Text.Trim(), _txtBacktestPort.Text.Trim(), _txtDashPort.Text.Trim() };
        foreach (var p in ports)
        {
            StopByListeningPort(p);
        }
        for (var i = 0; i < 10; i++)
        {
            var anyListening = false;
            foreach (var p in ports)
            {
                if (!int.TryParse(p, out var port)) continue;
                if (IsPortListening(port))
                {
                    anyListening = true;
                    StopByListeningPort(p);
                }
            }
            if (!anyListening) return;
            await Task.Delay(300);
        }
    }

    private void StopTrackedProcesses()
    {
        var list = new List<Process?> { _watchdogProc, _testProc, _dashboardProc, _marketIntelProc, _marketDataProc, _newsProc, _collectorProc, _backtestProc, _apiProc };
        foreach (var p in list)
        {
            try
            {
                if (p != null && !p.HasExited)
                    p.Kill(entireProcessTree: true);
            }
            catch { }
        }
        _dashboardProc = null;
        _collectorProc = null;
        _newsProc = null;
        _marketDataProc = null;
        _marketIntelProc = null;
        _backtestProc = null;
        _apiProc = null;
        _testProc = null;
        _watchdogProc = null;
    }

    private void StopExternalProjectProcesses()
    {
        try
        {
            var root = _txtRoot.Text.Trim();
            var apiPort = _txtApiPort.Text.Trim();
            var backtestPort = _txtBacktestPort.Text.Trim();
            var dashPort = _txtDashPort.Text.Trim();

            StopByListeningPort(apiPort);
            StopByListeningPort(backtestPort);
            StopByListeningPort(dashPort);

            var rows = Process.GetProcesses();
            foreach (var proc in rows)
            {
                try
                {
                    var cmd = TryGetCommandLine(proc);
                    if (string.IsNullOrWhiteSpace(cmd)) continue;
                    var c = cmd.ToLowerInvariant();

                    var isProjectPython =
                        (
                            c.Contains(" -m uvicorn app:app") &&
                            (
                                c.Contains($"--port {apiPort}") ||
                                c.Contains($"--port {backtestPort}")
                            )
                        )
                        ||
                        c.Contains("data_collector.py") ||
                        c.Contains("news_worker.py") ||
                        c.Contains("market_data_worker.py") ||
                        c.Contains("market_intel_worker.py");

                    var isVite =
                        c.Contains("\\vite\\bin\\vite.js") ||
                        c.Contains(" /d /s /c vite");

                    if (!isProjectPython && !isVite) continue;

                    if (proc.Id == Process.GetCurrentProcess().Id) continue;
                    proc.Kill(entireProcessTree: true);
                    AppendLog(_logLauncher, $"Stopped external PID {proc.Id}: {proc.ProcessName}");
                }
                catch { }
                finally
                {
                    proc.Dispose();
                }
            }
        }
        catch (Exception ex)
        {
            AppendLog(_logLauncher, $"External stop warning: {ex.Message}");
        }
    }

    private void StopByListeningPort(string portText)
    {
        if (!int.TryParse(portText, out var port)) return;
        try
        {
            var pids = new HashSet<int>();
            var ipProps = IPGlobalProperties.GetIPGlobalProperties();
            foreach (var ep in ipProps.GetActiveTcpListeners())
            {
                if (ep.Port != port) continue;
                foreach (var pid in GetPidsFromNetstat(port))
                    pids.Add(pid);
                break;
            }
            if (pids.Count == 0)
            {
                foreach (var pid in GetPidsFromNetstat(port))
                    pids.Add(pid);
            }
            foreach (var pid in pids)
            {
                try
                {
                    if (pid == Process.GetCurrentProcess().Id) continue;
                    Process.GetProcessById(pid).Kill(entireProcessTree: true);
                    AppendLog(_logLauncher, $"Stopped PID {pid} on port {port}");
                }
                catch (Exception ex)
                {
                    try
                    {
                        var psi = new ProcessStartInfo
                        {
                            FileName = "taskkill",
                            Arguments = $"/PID {pid} /T /F",
                            UseShellExecute = false,
                            CreateNoWindow = true
                        };
                        using var tk = Process.Start(psi);
                        tk?.WaitForExit(1500);
                        AppendLog(_logLauncher, $"taskkill fallback attempted for PID {pid} on port {port}");
                    }
                    catch
                    {
                        AppendLog(_logLauncher, $"Failed to stop PID {pid} on port {port}: {ex.Message}");
                    }
                }
            }
        }
        catch { }
    }

    private static bool IsPortListening(int port)
    {
        try
        {
            var ipProps = IPGlobalProperties.GetIPGlobalProperties();
            foreach (var ep in ipProps.GetActiveTcpListeners())
            {
                if (ep.Port == port) return true;
            }
        }
        catch { }
        return false;
    }

    private static IEnumerable<int> GetPidsFromNetstat(int port)
    {
        var pids = new List<int>();
        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = "netstat",
                Arguments = "-ano -p tcp",
                RedirectStandardOutput = true,
                UseShellExecute = false,
                CreateNoWindow = true
            };
            using var p = Process.Start(psi);
            if (p == null) return pids;
            var output = p.StandardOutput.ReadToEnd();
            p.WaitForExit(2000);
            var lines = output.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
            foreach (var ln in lines)
            {
                if (!ln.Contains("LISTENING", StringComparison.OrdinalIgnoreCase)) continue;
                if (!ln.Contains($":{port}")) continue;
                var m = Regex.Match(ln, @"\s+(\d+)\s*$");
                if (!m.Success) continue;
                if (int.TryParse(m.Groups[1].Value, out var pid))
                    pids.Add(pid);
            }
        }
        catch { }
        return pids;
    }

    private static string? TryGetCommandLine(Process process)
    {
        try
        {
            using var searcher = new System.Management.ManagementObjectSearcher(
                $"SELECT CommandLine FROM Win32_Process WHERE ProcessId = {process.Id}");
            foreach (var obj in searcher.Get())
            {
                return obj?["CommandLine"]?.ToString();
            }
        }
        catch { }
        return null;
    }

    private bool IsExpectedTrackedProcess(Process? process, string component)
    {
        if (!IsAlive(process))
            return false;

        var cmd = TryGetCommandLine(process!);
        if (string.IsNullOrWhiteSpace(cmd))
            return false;

        var c = cmd.ToLowerInvariant();
        return component.ToLowerInvariant() switch
        {
            "api" => c.Contains(" -m uvicorn app:app") && c.Contains($"--port {_txtApiPort.Text.Trim()}"),
            "backtest" => c.Contains(" -m uvicorn app:app") && c.Contains($"--port {_txtBacktestPort.Text.Trim()}"),
            "collector" => c.Contains("data_collector.py"),
            "dashboard" => c.Contains("\\vite\\bin\\vite.js") && c.Contains($"--port {_txtDashPort.Text.Trim()}"),
            _ => false
        };
    }

    private void DeleteRuntimeInfo(RuntimeComponentInfo? info)
    {
        if (info == null || string.IsNullOrWhiteSpace(info.RuntimePath))
            return;

        try
        {
            if (File.Exists(info.RuntimePath))
                File.Delete(info.RuntimePath);
        }
        catch
        {
        }
    }

    private Process? TryAttachExpectedProcess(string scope, string component, Func<int?> fallbackPidFactory)
    {
        var runtimeInfo = TryReadRuntimeInfo(scope, component);
        var runtimeProc = TryGetProcessById(runtimeInfo?.EffectivePid);
        if (IsExpectedTrackedProcess(runtimeProc, component))
            return runtimeProc;

        var fallbackProc = TryGetProcessById(fallbackPidFactory());
        if (IsExpectedTrackedProcess(fallbackProc, component))
            return fallbackProc;

        if (runtimeInfo != null)
            DeleteRuntimeInfo(runtimeInfo);

        return null;
    }

    private static int? TryGetFileAgeSeconds(string path)
    {
        try
        {
            if (!File.Exists(path))
                return null;
            var age = DateTime.UtcNow - File.GetLastWriteTimeUtc(path);
            return Math.Max(0, (int)Math.Round(age.TotalSeconds));
        }
        catch
        {
            return null;
        }
    }

    private static string FormatAge(int? ageSec)
    {
        if (ageSec is null)
            return "n/a";
        if (ageSec.Value < 60)
            return $"{ageSec.Value}s";
        if (ageSec.Value < 3600)
            return $"{ageSec.Value / 60}m";
        return $"{ageSec.Value / 3600}h";
    }

    private static string? TryReadLastLine(string path)
    {
        try
        {
            if (!File.Exists(path))
                return null;
            var lines = File.ReadAllLines(path);
            for (var i = lines.Length - 1; i >= 0; i--)
            {
                var line = lines[i]?.Trim();
                if (!string.IsNullOrWhiteSpace(line))
                    return line;
            }
        }
        catch
        {
        }
        return null;
    }

    private void UpdateRuntimeFromTestArtifacts(string suiteKey, string runDir, RichTextBox watchdogLog, RichTextBox testLog, RichTextBox runnerLog)
    {
        try
        {
            var watchdogLogPath = Path.Combine(runDir, "watchdog.log");
            var heartbeatPath = Path.Combine(runDir, "heartbeat.json");
            var runLogPath = Path.Combine(runDir, "run.log");

            var lastWatchdogLine = TryReadLastLine(watchdogLogPath);
            _lastWatchdogRuntimeLines.TryGetValue(suiteKey, out var lastWatchdogCache);
            if (!string.IsNullOrWhiteSpace(lastWatchdogLine) && !string.Equals(lastWatchdogLine, lastWatchdogCache, StringComparison.Ordinal))
            {
                _lastWatchdogRuntimeLines[suiteKey] = lastWatchdogLine;
                AppendLog(watchdogLog, lastWatchdogLine);
            }

            if (File.Exists(heartbeatPath))
            {
                using var doc = JsonDocument.Parse(File.ReadAllText(heartbeatPath));
                var root = doc.RootElement;
                var t = root.TryGetProperty("t", out var tProp) ? tProp.GetString() ?? "" : "";
                var state = root.TryGetProperty("state", out var sProp) ? sProp.GetString() ?? "" : "";
                var pid = root.TryGetProperty("pid", out var pProp) ? pProp.ToString() : "";
                var key = $"{t}|{state}|{pid}";
                _lastTestHeartbeatRuntimeKeys.TryGetValue(suiteKey, out var lastHeartbeatCache);
                if (!string.IsNullOrWhiteSpace(t) && !string.Equals(key, lastHeartbeatCache, StringComparison.Ordinal))
                {
                    _lastTestHeartbeatRuntimeKeys[suiteKey] = key;
                    AppendLog(testLog, $"HEARTBEAT t={t} state={state} pid={pid}");
                }
            }

            var lastRunnerLine = TryReadLastLine(runLogPath);
            _lastRunnerRuntimeLines.TryGetValue(suiteKey, out var lastRunnerCache);
            if (!string.IsNullOrWhiteSpace(lastRunnerLine) && !string.Equals(lastRunnerLine, lastRunnerCache, StringComparison.Ordinal))
            {
                _lastRunnerRuntimeLines[suiteKey] = lastRunnerLine;
                AppendLog(runnerLog, lastRunnerLine);
            }
        }
        catch
        {
        }
    }

    private static List<(int ProcessId, string CommandLine)> FindProcesses(string processName, string needle)
    {
        var outList = new List<(int ProcessId, string CommandLine)>();
        try
        {
            using var searcher = new ManagementObjectSearcher($"SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name = '{processName}'");
            foreach (var obj in searcher.Get())
            {
                var cmd = obj?["CommandLine"]?.ToString() ?? string.Empty;
                if (string.IsNullOrWhiteSpace(cmd) || cmd.IndexOf(needle, StringComparison.OrdinalIgnoreCase) < 0)
                    continue;
                if (int.TryParse(obj?["ProcessId"]?.ToString(), out var pid))
                    outList.Add((pid, cmd));
            }
        }
        catch
        {
        }
        return outList;
    }

    private static int? FindFirstProcessId(string processName, string needle)
    {
        return FindProcesses(processName, needle).Select(x => (int?)x.ProcessId).FirstOrDefault();
    }

    private static Process? TryGetProcessById(int? pid)
    {
        if (pid is null || pid <= 0) return null;
        try
        {
            return Process.GetProcessById(pid.Value);
        }
        catch
        {
            return null;
        }
    }

    private static int? TryResolveLeafChildPid(int pid)
    {
        if (pid <= 0) return null;
        try
        {
            try
            {
                _ = Process.GetProcessById(pid);
            }
            catch
            {
                return null;
            }

            var current = pid;
            var seen = new HashSet<int>();
            while (seen.Add(current))
            {
                var children = new List<(int ProcessId, string Name)>();
                using var searcher = new ManagementObjectSearcher(
                    $"SELECT ProcessId, Name FROM Win32_Process WHERE ParentProcessId = {current}");
                foreach (var obj in searcher.Get())
                {
                    if (int.TryParse(obj?["ProcessId"]?.ToString(), out var childPid) && childPid > 0)
                        children.Add((childPid, obj?["Name"]?.ToString() ?? string.Empty));
                }

                if (children.Count == 0)
                    return current;

                var preferred = children
                    .Where(child =>
                    {
                        var name = child.Name.ToLowerInvariant();
                        return name.Contains("python") || name.Contains("node") || name.Contains("cmd");
                    })
                    .Select(child => (int?)child.ProcessId)
                    .FirstOrDefault();

                current = preferred ?? children.Max(child => child.ProcessId);
            }
        }
        catch
        {
        }
        return pid;
    }

    private RuntimeComponentInfo? TryReadRuntimeInfo(string scope, string component)
    {
        try
        {
            var path = Path.Combine(ProjectDirPath(), "_runtime", scope, component + ".json");
            if (!File.Exists(path))
                return null;

            using var doc = JsonDocument.Parse(File.ReadAllText(path));
            var root = doc.RootElement;

            static int? ReadPid(JsonElement root, string property)
            {
                if (!root.TryGetProperty(property, out var value))
                    return null;
                if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var numericPid) && numericPid > 0)
                    return numericPid;
                if (int.TryParse(value.ToString(), out var stringPid) && stringPid > 0)
                    return stringPid;
                return null;
            }

            static string? ReadString(JsonElement root, string property)
            {
                if (!root.TryGetProperty(property, out var value))
                    return null;
                var text = value.ToString()?.Trim();
                return string.IsNullOrWhiteSpace(text) ? null : text;
            }

            var wrapperPid = ReadPid(root, "wrapper_pid");
            var childPid = ReadPid(root, "child_pid");
            var recordedPid = ReadPid(root, "pid");
            var effectivePid =
                (childPid.HasValue ? TryResolveLeafChildPid(childPid.Value) : null) ??
                (recordedPid.HasValue ? TryResolveLeafChildPid(recordedPid.Value) : null) ??
                (wrapperPid.HasValue ? TryResolveLeafChildPid(wrapperPid.Value) : null);

            return new RuntimeComponentInfo
            {
                Scope = scope,
                Component = component,
                WrapperPid = wrapperPid,
                ChildPid = childPid,
                RecordedPid = recordedPid,
                EffectivePid = effectivePid,
                Command = ReadString(root, "command"),
                Workdir = ReadString(root, "workdir"),
                OutputRoot = ReadString(root, "output_root"),
                RuntimePath = path
            };
        }
        catch
        {
        }
        return null;
    }

    private int? TryReadRuntimePid(string scope, string component)
    {
        return TryReadRuntimeInfo(scope, component)?.EffectivePid;
    }

    private static string FormatRuntimePid(int? pid)
    {
        return pid.HasValue && pid.Value > 0 ? pid.Value.ToString() : "-";
    }

    private static string ShortRuntimePath(string? path)
    {
        if (string.IsNullOrWhiteSpace(path))
            return "-";
        var normalized = path.Trim();
        return normalized.Replace(@"C:\aiinvest\", string.Empty, StringComparison.OrdinalIgnoreCase);
    }

    private void UpdateRuntimeDiagnosticLine(string key, string line)
    {
        _lastRuntimeDiagnosticLines.TryGetValue(key, out var previous);
        if (string.Equals(previous, line, StringComparison.Ordinal))
            return;

        _lastRuntimeDiagnosticLines[key] = line;
        AppendLog(_logControlRuntime, line);
    }

    private void UpdateRuntimeDiagnostics(string key, string label, bool healthy, RuntimeComponentInfo? info, string detail)
    {
        var line = string.Join(" | ", new[]
        {
            label,
            healthy ? "healthy=yes" : "healthy=no",
            $"wrapper={FormatRuntimePid(info?.WrapperPid)}",
            $"child={FormatRuntimePid(info?.ChildPid)}",
            $"recorded={FormatRuntimePid(info?.RecordedPid)}",
            $"effective={FormatRuntimePid(info?.EffectivePid)}",
            detail,
            $"workdir={ShortRuntimePath(info?.Workdir)}",
            $"output={ShortRuntimePath(info?.OutputRoot)}"
        });
        UpdateRuntimeDiagnosticLine(key, line);
    }

    private static List<(int ProcessId, string ProcessName, string CommandLine)> FindProcessesByNeedle(string needle)
    {
        var outList = new List<(int ProcessId, string ProcessName, string CommandLine)>();
        try
        {
            using var searcher = new ManagementObjectSearcher("SELECT ProcessId, Name, CommandLine FROM Win32_Process");
            foreach (var obj in searcher.Get())
            {
                var cmd = obj?["CommandLine"]?.ToString() ?? string.Empty;
                if (string.IsNullOrWhiteSpace(cmd) || cmd.IndexOf(needle, StringComparison.OrdinalIgnoreCase) < 0)
                    continue;
                if (!int.TryParse(obj?["ProcessId"]?.ToString(), out var pid))
                    continue;
                var name = obj?["Name"]?.ToString() ?? string.Empty;
                outList.Add((pid, name, cmd));
            }
        }
        catch
        {
        }
        return outList;
    }

    private string ShadowTestsRoot() => Path.Combine(ReportsRootDir(), "_shadow_tests");
    private string ShadowReportsRoot() => Path.Combine(ReportsRootDir(), "_shadow-reports");
    private string GetCryptoShadowRunDir() => GetPreferredShadowRunDir("bin_krak");
    private string GetIbkrShadowRunDir() => GetPreferredShadowRunDir("ibkr");
    private string GetIbkrShadowReportsDir() => Path.Combine(ShadowReportsRoot(), "ibkr");
    private const int ShadowRunExpiryGraceSeconds = 1800;

    private sealed class ShadowRunStateSnapshot
    {
        public bool Completed { get; init; }
        public DateTimeOffset? StartedAt { get; init; }
        public int TargetDurationSec { get; init; }
    }

    private sealed class RuntimeComponentInfo
    {
        public string Scope { get; init; } = string.Empty;
        public string Component { get; init; } = string.Empty;
        public int? WrapperPid { get; init; }
        public int? ChildPid { get; init; }
        public int? RecordedPid { get; init; }
        public int? EffectivePid { get; init; }
        public string? Command { get; init; }
        public string? Workdir { get; init; }
        public string? OutputRoot { get; init; }
        public string? RuntimePath { get; init; }
    }

    private string GetSuiteRunDirName(string suiteKey)
    {
        return string.Equals(suiteKey, "ibkr", StringComparison.OrdinalIgnoreCase) ? "ibkr" : "bin_krak";
    }

    private IEnumerable<DirectoryInfo> EnumerateShadowRunDirs(string suiteKey)
    {
        var root = ShadowTestsRoot();
        Directory.CreateDirectory(root);
        var info = new DirectoryInfo(root);

        if (string.Equals(suiteKey, "ibkr", StringComparison.OrdinalIgnoreCase))
        {
            var legacyIbkr = new DirectoryInfo(Path.Combine(root, "shadow-suite-ibkr"));
            if (legacyIbkr.Exists)
                yield return legacyIbkr;
        }
        else
        {
            foreach (var legacy in info.GetDirectories("shadow-suite-*").Where(IsCryptoShadowRunDir))
                yield return legacy;
        }

        foreach (var weeklyRoot in info.GetDirectories("weekly-suite-*"))
        {
            var candidate = new DirectoryInfo(Path.Combine(weeklyRoot.FullName, GetSuiteRunDirName(suiteKey)));
            if (candidate.Exists)
                yield return candidate;
        }
    }

    private static bool TryReadShadowRunState(string runDir, out ShadowRunStateSnapshot snapshot)
    {
        snapshot = new ShadowRunStateSnapshot();
        try
        {
            var statePath = Path.Combine(runDir, "state.json");
            if (!File.Exists(statePath))
                return false;

            using var doc = JsonDocument.Parse(File.ReadAllText(statePath));
            var root = doc.RootElement;
            var completed = root.TryGetProperty("completed", out var completedProp) && completedProp.ValueKind == JsonValueKind.True;
            DateTimeOffset? startedAt = null;
            if (root.TryGetProperty("started_at", out var startedAtProp))
            {
                var startedAtRaw = startedAtProp.GetString();
                if (!string.IsNullOrWhiteSpace(startedAtRaw) && DateTimeOffset.TryParse(startedAtRaw, out var startedAtParsed))
                    startedAt = startedAtParsed;
            }
            var targetDurationSec = 0;
            if (root.TryGetProperty("target_duration_sec", out var durationProp) && durationProp.ValueKind == JsonValueKind.Number)
            {
                try { targetDurationSec = durationProp.GetInt32(); } catch { targetDurationSec = 0; }
            }
            snapshot = new ShadowRunStateSnapshot
            {
                Completed = completed,
                StartedAt = startedAt,
                TargetDurationSec = targetDurationSec
            };
            return true;
        }
        catch
        {
            return false;
        }
    }

    private static bool IsShadowRunExpired(ShadowRunStateSnapshot snapshot)
    {
        if (snapshot.Completed)
            return false;
        if (snapshot.StartedAt is null || snapshot.TargetDurationSec <= 0)
            return false;
        var deadline = snapshot.StartedAt.Value.AddSeconds(snapshot.TargetDurationSec + ShadowRunExpiryGraceSeconds);
        return DateTimeOffset.Now >= deadline;
    }

    private static bool IsShadowRunActive(ShadowRunStateSnapshot snapshot)
    {
        return !snapshot.Completed && !IsShadowRunExpired(snapshot);
    }

    private string? GetActiveWeeklySuiteRoot()
    {
        var root = ShadowTestsRoot();
        var info = new DirectoryInfo(root);
        foreach (var weeklyRoot in info.GetDirectories("weekly-suite-*").OrderByDescending(d => d.LastWriteTimeUtc))
        {
            foreach (var suiteDirName in new[] { "bin_krak", "ibkr" })
            {
                var suiteDir = new DirectoryInfo(Path.Combine(weeklyRoot.FullName, suiteDirName));
                if (!suiteDir.Exists)
                    continue;
                if (TryReadShadowRunState(suiteDir.FullName, out var snapshot) && IsShadowRunActive(snapshot))
                    return weeklyRoot.FullName;
            }
        }
        return null;
    }

    private string GetOrCreateShadowRunDirForStart(string suiteKey)
    {
        foreach (var dir in EnumerateShadowRunDirs(suiteKey).OrderByDescending(d => d.LastWriteTimeUtc))
        {
            if (TryReadShadowRunState(dir.FullName, out var snapshot) && IsShadowRunActive(snapshot))
                return dir.FullName;
        }

        var root = ShadowTestsRoot();
        Directory.CreateDirectory(root);

        var activeWeeklyRoot = GetActiveWeeklySuiteRoot();
        if (string.IsNullOrWhiteSpace(activeWeeklyRoot))
        {
            var weeklyRootName = $"weekly-suite-{DateTime.Now:yyyyMMdd-HHmmss}";
            activeWeeklyRoot = Path.Combine(root, weeklyRootName);
            Directory.CreateDirectory(activeWeeklyRoot);
        }

        var suiteRunDir = Path.Combine(activeWeeklyRoot, GetSuiteRunDirName(suiteKey));
        Directory.CreateDirectory(suiteRunDir);
        return suiteRunDir;
    }

    private static bool IsCryptoShadowRunDir(DirectoryInfo dir)
    {
        var name = dir.Name.Trim();
        if (name.Equals("shadow-suite-ibkr", StringComparison.OrdinalIgnoreCase))
            return false;
        if (name.StartsWith("weekly-suite-", StringComparison.OrdinalIgnoreCase))
            return false;
        return true;
    }

    private string GetPreferredShadowRunDir(string suiteKey)
    {
        var dirs = EnumerateShadowRunDirs(suiteKey)
            .OrderByDescending(d => d.LastWriteTimeUtc)
            .ToList();

        foreach (var dir in dirs)
        {
            if (TryReadShadowRunState(dir.FullName, out var snapshot) && IsShadowRunActive(snapshot))
                return dir.FullName;
        }

        if (dirs.Count > 0)
            return dirs[0].FullName;

        return GetOrCreateShadowRunDirForStart(suiteKey);
    }

    private int? FindTestProcessId(string runDir)
    {
        var all = FindProcesses("powershell.exe", "shadow_trading_test_suite.ps1");
        foreach (var row in all)
        {
            if (row.CommandLine.IndexOf(runDir, StringComparison.OrdinalIgnoreCase) >= 0)
                return row.ProcessId;
        }
        return null;
    }

    private int? FindTestProcessId()
    {
        return FindTestProcessId(GetCryptoShadowRunDir());
    }

    private int? FindWatchdogProcessId(string runDir)
    {
        var all = FindProcesses("powershell.exe", "shadow_trading_watchdog.ps1");
        foreach (var row in all)
        {
            if (row.CommandLine.IndexOf(runDir, StringComparison.OrdinalIgnoreCase) >= 0)
                return row.ProcessId;
        }
        return null;
    }

    private int? FindWatchdogProcessId()
    {
        return FindWatchdogProcessId(GetCryptoShadowRunDir());
    }

    private int? FindCollectorProcessId()
    {
        var runtimePid = TryReadRuntimePid("main", "collector");
        if (runtimePid.HasValue)
            return runtimePid;

        var all = FindProcessesByNeedle("data_collector.py")
            .Where(x => x.ProcessName.IndexOf("python", StringComparison.OrdinalIgnoreCase) >= 0)
            .OrderByDescending(x => x.CommandLine.IndexOf(@"\venv\Scripts\python.exe", StringComparison.OrdinalIgnoreCase) >= 0)
            .ThenByDescending(x => x.ProcessId)
            .ToList();
        return all.Select(x => (int?)x.ProcessId).FirstOrDefault();
    }

    private bool IsProjectRunning()
    {
        NormalizeProcessRefs();
        if (IsAlive(_apiProc) || IsAlive(_backtestProc) || IsAlive(_collectorProc) || IsAlive(_marketDataProc) || IsAlive(_dashboardProc))
            return true;
        if (int.TryParse(_txtApiPort.Text.Trim(), out var apiPort) && IsPortListening(apiPort)) return true;
        if (int.TryParse(_txtBacktestPort.Text.Trim(), out var backtestPort) && IsPortListening(backtestPort)) return true;
        if (int.TryParse(_txtDashPort.Text.Trim(), out var dashPort) && IsPortListening(dashPort)) return true;
        return false;
    }

    private async Task StartTestAsync()
    {
        try
        {
            var runDir = GetOrCreateShadowRunDirForStart("bin_krak");
            var existingPid = FindTestProcessId(runDir);
            if (existingPid.HasValue)
            {
                _testProc = TryGetProcessById(existingPid);
                AppendLog(_logLauncher, $"TEST already running (pid {existingPid.Value}).");
                return;
            }

            var watchdogPid = FindWatchdogProcessId(runDir);
            if (watchdogPid.HasValue)
            {
                _watchdogProc = TryGetProcessById(watchdogPid);
                AppendLog(_logLauncher, $"TEST start delegated to WATCHDOG (pid {watchdogPid.Value}).");
                return;
            }

            var root = RepoRoot();
            Directory.CreateDirectory(runDir);
            _testProc = StartProcess(
                "powershell.exe",
                $"-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"{ProjectScriptPath("shadow_trading_test_suite.ps1")}\" -RunDir \"{runDir}\" -DurationHours 168 -SampleMinutes 20 -HealStack",
                root,
                "TEST",
                _logTest
            );
            AppendLog(_logLauncher, $"TEST start requested for {runDir}.");
            await Task.Delay(300);
        }
        catch (Exception ex)
        {
            AppendLog(_logLauncher, $"TEST start failed: {ex.Message}");
        }
    }

    private async Task StartWatchdogAsync()
    {
        try
        {
            var runDir = GetOrCreateShadowRunDirForStart("bin_krak");
            var existingPid = FindWatchdogProcessId(runDir);
            if (existingPid.HasValue)
            {
                _watchdogProc = TryGetProcessById(existingPid);
                AppendLog(_logLauncher, $"WATCHDOG already running (pid {existingPid.Value}).");
                await EnsureWeeklyReportSchedulerAsync(runDir);
                return;
            }

            var root = RepoRoot();
            Directory.CreateDirectory(runDir);
            _watchdogProc = StartProcess(
                "powershell.exe",
                $"-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"{ProjectScriptPath("shadow_trading_watchdog.ps1")}\" -ProjectRoot \"{root}\" -RunDir \"{runDir}\" -DurationHours 168 -SampleMinutes 20 -HealStack",
                root,
                "WATCHDOG",
                _logWatchdog
            );
            AppendLog(_logLauncher, $"WATCHDOG start requested for {runDir}.");
            await EnsureWeeklyReportSchedulerAsync(runDir);
            await Task.Delay(300);
        }
        catch (Exception ex)
        {
            AppendLog(_logLauncher, $"WATCHDOG start failed: {ex.Message}");
        }
    }

    private async Task StartIbkrSuiteStackAsync()
    {
        try
        {
            if (await IsOkAsync("http://127.0.0.1:8110/health"))
            {
                AppendLog(_logLauncher, "IBKR suite API already running on :8110.");
                return;
            }

            var root = RepoRoot();
            StartProcess(
                "powershell.exe",
                $"-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"{ProjectScriptPath("start_ibkr_shadow_stack.ps1")}\" -ProjectRoot \"{root}\" -ApiPort 8110 -BacktestPort 8101",
                root,
                "IBKR-SUITE",
                _logIbkr
            );
            AppendLog(_logLauncher, "IBKR suite start requested.");
            await Task.Delay(1200);
        }
        catch (Exception ex)
        {
            AppendLog(_logLauncher, $"IBKR suite start failed: {ex.Message}");
        }
    }

    private async Task StartIbkrWatchdogAsync()
    {
        try
        {
            var runDir = GetOrCreateShadowRunDirForStart("ibkr");
            var existingPid = FindWatchdogProcessId(runDir);
            if (existingPid.HasValue)
            {
                _ibkrWatchdogProc = TryGetProcessById(existingPid);
                AppendLog(_logLauncher, $"IBKR WATCHDOG already running (pid {existingPid.Value}).");
                await EnsureWeeklyReportSchedulerAsync(runDir);
                return;
            }

            var root = RepoRoot();
            Directory.CreateDirectory(runDir);
            Directory.CreateDirectory(GetIbkrShadowReportsDir());
            _ibkrWatchdogProc = StartProcess(
                "powershell.exe",
                $"-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"{ProjectScriptPath("shadow_trading_watchdog.ps1")}\" -ProjectRoot \"{root}\" -RunDir \"{runDir}\" -ApiBase \"http://127.0.0.1:8110\" -ReportOutputDir \"{GetIbkrShadowReportsDir()}\" -SuiteName \"ibkr\" -HealScript \"{ProjectScriptPath("start_ibkr_shadow_stack.ps1")}\" -DurationHours 168 -SampleMinutes 20 -HealStack",
                root,
                "IBKR-WATCHDOG",
                _logIbkrWatchdog
            );
            AppendLog(_logLauncher, $"IBKR WATCHDOG start requested for {runDir}.");
            await EnsureWeeklyReportSchedulerAsync(runDir);
            await Task.Delay(300);
        }
        catch (Exception ex)
        {
            AppendLog(_logLauncher, $"IBKR WATCHDOG start failed: {ex.Message}");
        }
    }

    private async Task EnsureIbkrSuiteAutostartAsync()
    {
        var runDir = GetOrCreateShadowRunDirForStart("ibkr");
        var existingPid = FindWatchdogProcessId(runDir);
        if (existingPid.HasValue)
        {
            _ibkrWatchdogProc = TryGetProcessById(existingPid);
            AppendLog(_logLauncher, $"IBKR suite auto-start skipped: watchdog already running (pid {existingPid.Value}).");
            await EnsureWeeklyReportSchedulerAsync(runDir);
            return;
        }
        AppendLog(_logLauncher, "IBKR suite auto-start check passed after 60s.");
        await StartIbkrSuiteStackAsync();
        await StartIbkrWatchdogAsync();
    }

    private static string? TryGetWeeklySuiteRootFromRunDir(string runDir)
    {
        try
        {
            var dir = new DirectoryInfo(runDir);
            if (!dir.Exists || dir.Parent is null)
                return null;
            return dir.Parent.Name.StartsWith("weekly-suite-", StringComparison.OrdinalIgnoreCase)
                ? dir.Parent.FullName
                : null;
        }
        catch
        {
            return null;
        }
    }

    private string GetWeeklyReportsRoot(string weeklySuiteRoot)
    {
        var weeklyName = new DirectoryInfo(weeklySuiteRoot).Name;
        var suffix = weeklyName.StartsWith("weekly-suite-", StringComparison.OrdinalIgnoreCase)
            ? weeklyName["weekly-suite-".Length..]
            : weeklyName;
        return Path.Combine(ShadowReportsRoot(), $"weekly_{suffix}", "combined");
    }

    private int? FindWeeklyReportSchedulerProcessId(string mainRunDir, string ibkrRunDir)
    {
        var all = FindProcesses("powershell.exe", "schedule_weekly_shadow_reports.ps1");
        foreach (var row in all)
        {
            if (row.CommandLine.IndexOf(mainRunDir, StringComparison.OrdinalIgnoreCase) >= 0 &&
                row.CommandLine.IndexOf(ibkrRunDir, StringComparison.OrdinalIgnoreCase) >= 0)
                return row.ProcessId;
        }
        return null;
    }

    private async Task EnsureWeeklyReportSchedulerAsync(string runDir)
    {
        try
        {
            var weeklyRoot = TryGetWeeklySuiteRootFromRunDir(runDir);
            if (string.IsNullOrWhiteSpace(weeklyRoot))
                return;

            var mainRunDir = Path.Combine(weeklyRoot, "bin_krak");
            var ibkrRunDir = Path.Combine(weeklyRoot, "ibkr");
            Directory.CreateDirectory(mainRunDir);
            Directory.CreateDirectory(ibkrRunDir);

            var existingPid = FindWeeklyReportSchedulerProcessId(mainRunDir, ibkrRunDir);
            if (existingPid.HasValue)
            {
                AppendLog(_logLauncher, $"WEEKLY REPORT scheduler already running (pid {existingPid.Value}).");
                return;
            }

            var root = RepoRoot();
            var outputDir = GetWeeklyReportsRoot(weeklyRoot);
            Directory.CreateDirectory(outputDir);
            StartProcess(
                "powershell.exe",
                $"-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File \"{ProjectScriptPath("schedule_weekly_shadow_reports.ps1")}\" -ProjectRoot \"{root}\" -MainRunDir \"{mainRunDir}\" -IbkrRunDir \"{ibkrRunDir}\" -OutputDir \"{outputDir}\" -Label \"{new DirectoryInfo(weeklyRoot).Name}\" -DailyHour 15",
                root,
                "WEEKLY-REPORT",
                _logLauncher
            );
            AppendLog(_logLauncher, $"WEEKLY REPORT scheduler start requested for {weeklyRoot}.");
            await Task.Delay(300);
        }
        catch (Exception ex)
        {
            AppendLog(_logLauncher, $"WEEKLY REPORT scheduler start failed: {ex.Message}");
        }
    }

    private async Task EnsureWatchdogAutostartAsync()
    {
        var existingPid = FindWatchdogProcessId(GetCryptoShadowRunDir());
        if (existingPid.HasValue)
        {
            _watchdogProc = TryGetProcessById(existingPid);
            AppendLog(_logLauncher, $"Watchdog auto-start skipped: already running (pid {existingPid.Value}).");
            await EnsureWeeklyReportSchedulerAsync(GetCryptoShadowRunDir());
        }
        else
        {
            AppendLog(_logLauncher, "Watchdog auto-start check passed after 60s.");
            await StartWatchdogAsync();
        }
        await EnsureIbkrSuiteAutostartAsync();
    }

    private async Task StartBotAsync(bool skipProjectActionLock = false)
    {
        Exception? lastErr = null;
        if (!skipProjectActionLock && IsProjectActionLocked())
        {
            AppendLog(_logLauncher, "Ignored manual bot start: project start/stop/restart is already in progress.");
            return;
        }
        for (var i = 0; i < 4; i++)
        {
            try
            {
                var health = await IsStrictOkAsync($"{ApiBase()}/health");
                if (!health)
                {
                    await Task.Delay(600);
                    continue;
                }
                using var req = new HttpRequestMessage(HttpMethod.Post, $"{ApiBase()}/bot/start");
                var res = await _http.SendAsync(req);
                if (!res.IsSuccessStatusCode)
                {
                    var body = await res.Content.ReadAsStringAsync();
                    lastErr = new Exception($"HTTP {(int)res.StatusCode}: {body}");
                    await Task.Delay(600);
                    continue;
                }
                AppendLog(_logLauncher, "Bot start requested.");
                return;
            }
            catch (Exception ex)
            {
                lastErr = ex;
                await Task.Delay(600);
            }
        }
        AppendLog(_logLauncher, $"Bot start error: {lastErr?.Message ?? "API not ready"}");
    }

    private async Task EnsureBotAutostartAsync(long actionId)
    {
        if (Interlocked.Exchange(ref _botAutostartInProgress, 1) == 1)
        {
            AppendLog(_logLauncher, "Ignored duplicate bot auto-start: auto-start is already in progress.");
            return;
        }
        var until = DateTime.UtcNow.AddMinutes(2);
        try
        {
            while (DateTime.UtcNow < until)
            {
                if (!IsCurrentProjectAction(actionId))
                    return;
                try
                {
                    if (!await IsStrictOkAsync($"{ApiBase()}/health"))
                    {
                        await Task.Delay(1500);
                        continue;
                    }

                    if (!IsCurrentProjectAction(actionId))
                        return;

                    using var req = new HttpRequestMessage(HttpMethod.Post, $"{ApiBase()}/bot/start");
                    var res = await _http.SendAsync(req);
                    if (res.IsSuccessStatusCode)
                    {
                        if (!IsCurrentProjectAction(actionId))
                            return;
                        AppendLog(_logLauncher, "Bot auto-start requested.");
                        return;
                    }
                }
                catch
                {
                }
                await Task.Delay(1500);
            }
            AppendLog(_logLauncher, "Bot auto-start timeout (API not ready long enough).");
        }
        finally
        {
            Volatile.Write(ref _botAutostartInProgress, 0);
        }
    }

    private async Task StopBotAsync(bool skipProjectActionLock = false)
    {
        try
        {
            if (!skipProjectActionLock && IsProjectActionLocked())
            {
                AppendLog(_logLauncher, "Ignored manual bot stop: project start/stop/restart is already in progress.");
                return;
            }
            using var req = new HttpRequestMessage(HttpMethod.Post, $"{ApiBase()}/bot/stop?reason=manual_stop");
            _ = await _http.SendAsync(req);
            AppendLog(_logLauncher, "Bot stop requested.");
        }
        catch (Exception ex)
        {
            AppendLog(_logLauncher, $"Bot stop error: {ex.Message}");
        }
    }

    private void OpenDashboardExternal()
    {
        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = DashboardUrl(),
                UseShellExecute = true
            });
        }
        catch (Exception ex)
        {
            AppendLog(_logLauncher, $"Open browser failed: {ex.Message}");
        }
    }

    private Process StartProcess(string file, string args, string workDir, string name, RichTextBox? sink = null)
    {
        var psi = new ProcessStartInfo
        {
            FileName = file,
            Arguments = args,
            WorkingDirectory = workDir,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
            CreateNoWindow = true
        };
        if (file.EndsWith("python.exe", StringComparison.OrdinalIgnoreCase))
        {
            psi.EnvironmentVariables["PYTHONUTF8"] = "1";
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        }
        var shouldPersistProcessIo =
            string.Equals(name, "API", StringComparison.OrdinalIgnoreCase) ||
            string.Equals(name, "BACKTEST", StringComparison.OrdinalIgnoreCase) ||
            string.Equals(name, "COLLECTOR", StringComparison.OrdinalIgnoreCase);
        var p = new Process { StartInfo = psi, EnableRaisingEvents = true };
        p.OutputDataReceived += (_, e) =>
        {
            if (string.IsNullOrWhiteSpace(e.Data)) return;
            if (sink != null) AppendLog(sink, e.Data);
            if (shouldPersistProcessIo) WriteFileLog($"{name} | {e.Data}");
            if (!string.Equals(name, "API", StringComparison.OrdinalIgnoreCase) &&
                !string.Equals(name, "COLLECTOR", StringComparison.OrdinalIgnoreCase) &&
                !string.Equals(name, "BACKTEST", StringComparison.OrdinalIgnoreCase) &&
                !string.Equals(name, "TEST", StringComparison.OrdinalIgnoreCase) &&
                !string.Equals(name, "WATCHDOG", StringComparison.OrdinalIgnoreCase))
                AppendLog(_logControlRuntime, $"{name} | {e.Data}");
        };
        p.ErrorDataReceived += (_, e) =>
        {
            if (string.IsNullOrWhiteSpace(e.Data)) return;
            if (sink != null) AppendLog(sink, e.Data);
            if (shouldPersistProcessIo) WriteFileLog($"{name} | ERROR | {e.Data}");
            if (!string.Equals(name, "API", StringComparison.OrdinalIgnoreCase) &&
                !string.Equals(name, "COLLECTOR", StringComparison.OrdinalIgnoreCase) &&
                !string.Equals(name, "BACKTEST", StringComparison.OrdinalIgnoreCase) &&
                !string.Equals(name, "TEST", StringComparison.OrdinalIgnoreCase) &&
                !string.Equals(name, "WATCHDOG", StringComparison.OrdinalIgnoreCase))
                AppendLog(_logControlRuntime, $"{name} | {e.Data}");
        };
        p.Exited += (_, __) =>
        {
            try
            {
                WriteFileLog($"{name} | EXIT | code={(p.HasExited ? p.ExitCode : -1)}");
            }
            catch
            {
            }
            if (sink != null) AppendLog(sink, $"{name} exited.");
            AppendLog(_logLauncher, $"{name} exited.");
        };
        p.Start();
        p.BeginOutputReadLine();
        p.BeginErrorReadLine();
        WriteFileLog($"{name} | START | {file} {args} | wd={workDir}");
        AppendLog(_logLauncher, $"Started {name}: {file} {args}");
        return p;
    }

    private void StartDetachedProcess(string file, string args, string workDir, string name)
    {
        var psi = new ProcessStartInfo
        {
            FileName = file,
            Arguments = args,
            WorkingDirectory = workDir,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
            CreateNoWindow = true
        };
        var p = new Process { StartInfo = psi, EnableRaisingEvents = true };
        p.OutputDataReceived += (_, e) =>
        {
            if (string.IsNullOrWhiteSpace(e.Data)) return;
            WriteFileLog($"{name} | {e.Data}");
        };
        p.ErrorDataReceived += (_, e) =>
        {
            if (string.IsNullOrWhiteSpace(e.Data)) return;
            WriteFileLog($"{name} | ERROR | {e.Data}");
        };
        p.Exited += (_, __) =>
        {
            try
            {
                WriteFileLog($"{name} | EXIT | code={(p.HasExited ? p.ExitCode : -1)}");
            }
            catch
            {
            }
        };
        p.Start();
        p.BeginOutputReadLine();
        p.BeginErrorReadLine();
        WriteFileLog($"{name} | START | {file} {args} | wd={workDir}");
    }

    private async Task<bool> EnsureMongoDbAsync()
    {
        if (IsPortListeningLocal(27017))
            return true;

        var runtimeInfo = TryReadRuntimeInfo("main", "mongodb");
        var runtimeProc = TryGetProcessById(runtimeInfo?.EffectivePid);
        if (runtimeInfo != null && !IsAlive(runtimeProc))
        {
            DeleteRuntimeInfo(runtimeInfo);
            AppendLog(_logLauncher, "Removed stale MongoDB runtime metadata before restart.");
        }

        var mongoExe = MongoDaemonPath();
        var mongoData = MongoDataDirPath();
        if (!File.Exists(mongoExe))
        {
            AppendLog(_logLauncher, $"MongoDB start skipped: missing executable {mongoExe}");
            return false;
        }

        try
        {
            Directory.CreateDirectory(mongoData);
        }
        catch (Exception ex)
        {
            AppendLog(_logLauncher, $"MongoDB start failed: cannot prepare data dir {mongoData} ({ex.Message})");
            return false;
        }

        AppendLog(_logLauncher, $"Starting MongoDB: {mongoExe}");
        StartDetachedProcess(mongoExe, $"--dbpath \"{mongoData}\" --bind_ip 127.0.0.1 --port 27017", DatabaseRootDir(), "MONGODB");
        var deadline = DateTime.UtcNow.AddSeconds(15);
        while (DateTime.UtcNow < deadline)
        {
            if (IsPortListeningLocal(27017))
            {
                AppendLog(_logLauncher, "MongoDB is listening on 127.0.0.1:27017.");
                return true;
            }
            await Task.Delay(500);
        }

        AppendLog(_logLauncher, "MongoDB did not open port 27017 in time.");
        return false;
    }

    private void AppendLog(RichTextBox box, string line)
    {
        if (box == null || box.IsDisposed || box.Disposing || IsDisposed || Disposing) return;
        line = NormalizeLogLine(line);
        if (box.InvokeRequired)
        {
            try
            {
                if (!box.IsHandleCreated) return;
                box.BeginInvoke(new Action(() => AppendLog(box, line)));
            }
            catch
            {
                return;
            }
            return;
        }
        if (!box.IsHandleCreated || box.IsDisposed || box.Disposing) return;
        if (ReferenceEquals(box, _logControlRuntime))
            box.AppendText($"{line}{Environment.NewLine}");
        else
            box.AppendText($"[{DateTime.Now:HH:mm:ss}] {line}{Environment.NewLine}");
        box.SelectionStart = box.TextLength;
        box.ScrollToCaret();

        var boxName = ResolveLogBoxName(box);
        if (ShouldPersistLauncherFileLog(box, line))
        {
            WriteFileLog($"{boxName} | {line}");
        }
    }

    private bool IsControlUsable(Control? control)
    {
        return control != null &&
               !control.IsDisposed &&
               !control.Disposing &&
               control.IsHandleCreated &&
               !IsDisposed &&
               !Disposing;
    }

    private void SetRichTextBoxTextSafely(RichTextBox box, string text, bool moveCaretToStart)
    {
        if (box == null || IsDisposed || Disposing)
            return;
        if (box.InvokeRequired)
        {
            try
            {
                if (!IsControlUsable(box))
                    return;
                box.BeginInvoke(new Action(() => SetRichTextBoxTextSafely(box, text, moveCaretToStart)));
            }
            catch
            {
            }
            return;
        }
        if (!IsControlUsable(box))
            return;
        box.Text = text;
        box.SelectionStart = moveCaretToStart ? 0 : box.TextLength;
        box.ScrollToCaret();
    }

    private static string NormalizeLogLine(string line)
    {
        if (string.IsNullOrEmpty(line)) return string.Empty;
        return AnsiEscapeRegex.Replace(line, string.Empty);
    }

    private string ResolveLogBoxName(RichTextBox box)
    {
        if (ReferenceEquals(box, _logDashboard)) return "DASHBOARD";
        if (ReferenceEquals(box, _logAutoTune)) return "AUTO-TUNE";
        if (ReferenceEquals(box, _logShadowReport)) return "SHADOW-REPORT";
        if (ReferenceEquals(box, _logControlRuntime)) return "RUNTIME";
        if (ReferenceEquals(box, _logLauncher)) return "LAUNCHER";
        return "LOG";
    }

    private void WriteFileLog(string line)
    {
        try
        {
            var stamped = $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {line}{Environment.NewLine}";
            lock (_fileLogLock)
            {
                File.AppendAllText(_launcherLogPath, stamped);
            }
        }
        catch
        {
            // Keep launcher stable even on file I/O issues.
        }
    }

    private bool ShouldPersistLauncherFileLog(RichTextBox source, string line)
    {
        // Keep file logs compact: only launcher-level lifecycle and recovery/failure events.
        if (ReferenceEquals(source, _logLauncher)) return true;
        if (string.IsNullOrWhiteSpace(line)) return false;
        var l = line.Trim().ToLowerInvariant();
        if (l.Contains("exited")) return true; // process end/crash marker
        if (l.Contains("health transition")) return true; // up/down transitions
        if (l.Contains("reconnect")) return true; // interruption/recovery attempts
        return false;
    }

    private void LogHealthTransition(bool apiOk, bool dashOk)
    {
        if (apiOk)
        {
            _apiFailStreak = 0;
            if (!_lastApiOk.HasValue || _lastApiOk.Value != true)
            {
                AppendLog(_logLauncher, "API health transition: UP");
                _lastApiOk = true;
            }
        }
        else
        {
            _apiFailStreak++;
            if (_apiFailStreak >= 2 && (!_lastApiOk.HasValue || _lastApiOk.Value != false))
            {
                AppendLog(_logLauncher, "API health transition: DOWN");
                _lastApiOk = false;
            }
        }

        if (dashOk)
        {
            _dashFailStreak = 0;
            if (!_lastDashOk.HasValue || _lastDashOk.Value != true)
            {
                AppendLog(_logLauncher, "Dashboard health transition: UP");
                _lastDashOk = true;
            }
        }
        else
        {
            _dashFailStreak++;
            if (_dashFailStreak >= 2 && (!_lastDashOk.HasValue || _lastDashOk.Value != false))
            {
                AppendLog(_logLauncher, "Dashboard health transition: DOWN");
                _lastDashOk = false;
            }
        }
    }

    private async Task EnsureDashboardReconnectAsync(bool apiOk, bool dashOk)
    {
        if (!EmbeddedDashboardEnabled) return;
        if (dashOk || !apiOk || _web.CoreWebView2 == null) return;

        try
        {
            if (int.TryParse(_txtDashPort.Text.Trim(), out var dashPort) && !IsPortListening(dashPort))
            {
                // If dashboard port is down, try to restart dashboard process with cooldown.
                if ((DateTime.UtcNow - _lastDashboardStartAttemptUtc).TotalSeconds >= 20)
                {
                    _lastDashboardStartAttemptUtc = DateTime.UtcNow;
                    NormalizeProcessRefs();
                    if (!IsAlive(_dashboardProc))
                    {
                        var dashDir = DashboardDirPath();
                        var npm = @"C:\Program Files\nodejs\npm.cmd";
                        if (!File.Exists(npm))
                        {
                            try
                            {
                                var npmResolved = Process.Start(new ProcessStartInfo
                                {
                                    FileName = "where",
                                    Arguments = "npm.cmd",
                                    RedirectStandardOutput = true,
                                    UseShellExecute = false,
                                    CreateNoWindow = true
                                });
                                npmResolved?.WaitForExit(1000);
                                var line = npmResolved?.StandardOutput.ReadLine();
                                if (!string.IsNullOrWhiteSpace(line)) npm = line.Trim();
                            }
                            catch { }
                        }
                        if (Directory.Exists(dashDir))
                        {
                            _dashboardProc = StartProcess(
                                "powershell.exe",
                                $"-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command \"$env:VITE_API_BASE='http://127.0.0.1:{_txtApiPort.Text.Trim()}'; $env:VITE_BACKTEST_API_BASE='http://127.0.0.1:{_txtBacktestPort.Text.Trim()}'; & '{npm}' run -s preview -- --host 127.0.0.1 --port {_txtDashPort.Text.Trim()} --strictPort\"",
                                dashDir,
                                "DASHBOARD",
                                _logDashboard
                            );
                            AppendLog(_logLauncher, $"Dashboard self-heal restart requested on port {_txtDashPort.Text.Trim()}.");
                        }
                    }
                }
            }
        }
        catch (Exception ex)
        {
            AppendLog(_logLauncher, $"Dashboard self-heal failed: {ex.Message}");
        }

        var now = DateTime.UtcNow;
        if ((now - _lastWebReconnectAttemptUtc).TotalSeconds < 15) return;
        _lastWebReconnectAttemptUtc = now;
        try
        {
            var url = DashboardUrl();
            _web.CoreWebView2.Navigate(url);
            AppendLog(_logLauncher, $"Dashboard reconnect attempt: {url}");
        }
        catch (Exception ex)
        {
            AppendLog(_logLauncher, $"Dashboard reconnect failed: {ex.Message}");
        }
        await Task.CompletedTask;
    }

    private async Task RefreshHealthAsync()
    {
        if (_busy)
        {
            SetBadge(_lblBot, "Launcher busy: start/restart in progress...", BadgeTone.Neutral);
        }
        var apiOk = await IsStrictOkAsync($"{ApiBase()}/health");
        var backtestOk = await IsStrictOkAsync($"{BacktestBase()}/health");
        var dashOk = await IsStrictOkAsync(DashboardUrl(), timeoutSec: 5);
        var botState = apiOk ? await TryGetAsync($"{ApiBase()}/bot/status") : null;
        var now = DateTime.UtcNow;
        string? autoTuneLatest = null;
        if (apiOk && (now - _lastAutoTuneFetchUtc).TotalSeconds >= AutoTuneFetchSeconds)
        {
            autoTuneLatest = await TryGetAsync($"{ApiBase()}/bot/config-recommendations/latest");
            _lastAutoTuneFetchUtc = now;
        }

        string? shadowReport = null;
        if (apiOk && (now - _lastShadowFetchUtc).TotalSeconds >= ShadowFetchSeconds)
        {
            var shadowActions = Uri.EscapeDataString(_shadowActionsParam);
            shadowReport = await TryGetAsync($"{ApiBase()}/bot/signal-quality/shadow-report?lookback_hours=24&horizon_min=120&limit=2000&actions={shadowActions}");
            _lastShadowFetchUtc = now;
        }
        if (apiOk && (now - _lastTradedSymbolsFetchUtc).TotalSeconds >= TradedSymbolsFetchSeconds)
        {
            await UpdateTradedSymbolsLogAsync(force: false);
            _lastTradedSymbolsFetchUtc = now;
        }
        else if (!apiOk && string.IsNullOrWhiteSpace(_logTradedSymbols.Text))
        {
            SetRichTextBoxTextSafely(_logTradedSymbols, "Waiting for API...", moveCaretToStart: true);
        }
        LogHealthTransition(apiOk, dashOk);
        await EnsureDashboardReconnectAsync(apiOk, dashOk);
        if (!string.IsNullOrWhiteSpace(autoTuneLatest)) UpdateAutoTuneLog(autoTuneLatest);
        if (!string.IsNullOrWhiteSpace(shadowReport)) UpdateShadowReportLog(shadowReport);

        SetBadge(_lblApi, $"API {ApiBase()} {(apiOk ? "UP" : "DOWN")}", apiOk ? BadgeTone.Positive : BadgeTone.Negative);
        SetBadge(_lblDash, $"Dashboard {DashboardUrl()} {(dashOk ? "UP" : "DOWN")}", dashOk ? BadgeTone.Positive : BadgeTone.Negative);
        var newsWorker = ParseBoolField(botState, "news_worker");
        var intelWorker = ParseBoolField(botState, "market_intel_worker");
        var marketDataWorker = ParseBoolField(botState, "market_data_worker");
        var ibkrEnv = ReadLocalEnvFile();
        var ibkrTradingMode = GetConfiguredIbkrTradingMode(ibkrEnv);
        var ibkrHost = GetConfiguredIbkrHost(ibkrEnv);
        var configuredIbkrPort = GetConfiguredIbkrPort(ibkrEnv, ibkrTradingMode);
        var activeIbkrPort = FindListeningIbkrPort(ibkrHost, configuredIbkrPort, ibkrTradingMode);
        var ibkrPortOk = activeIbkrPort.HasValue;
        var ibkrProcOk = IsIbkrGatewayRunning();
        var ibkrOk = ibkrPortOk || ibkrProcOk;
        MaybeRecoverIbkrGatewayAsync(ibkrEnv, ibkrHost, configuredIbkrPort, ibkrTradingMode, ibkrPortOk, ibkrProcOk);
        var cryptoRunDir = GetCryptoShadowRunDir();
        var testPid = FindTestProcessId(cryptoRunDir);
        var watchdogPid = FindWatchdogProcessId(cryptoRunDir);
        var collectorPid = FindCollectorProcessId();
        var mainApiInfo = TryReadRuntimeInfo("main", "api");
        var mainBacktestInfo = TryReadRuntimeInfo("main", "backtest");
        var mainCollectorInfo = TryReadRuntimeInfo("main", "collector");
        var mainDashboardInfo = TryReadRuntimeInfo("main", "dashboard");
        _apiProc = TryGetProcessById(mainApiInfo?.EffectivePid) ?? _apiProc;
        _backtestProc = TryGetProcessById(mainBacktestInfo?.EffectivePid) ?? _backtestProc;
        _collectorProc = TryGetProcessById(mainCollectorInfo?.EffectivePid ?? collectorPid) ?? _collectorProc;
        _dashboardProc = TryGetProcessById(mainDashboardInfo?.EffectivePid) ?? _dashboardProc;
        _testProc = TryGetProcessById(testPid) ?? _testProc;
        _watchdogProc = TryGetProcessById(watchdogPid) ?? _watchdogProc;
        var heartbeatPath = Path.Combine(cryptoRunDir, "heartbeat.json");
        var watchdogLogPath = Path.Combine(cryptoRunDir, "watchdog.log");
        var testAgeSec = TryGetFileAgeSeconds(heartbeatPath);
        var watchdogAgeSec = TryGetFileAgeSeconds(watchdogLogPath);
        UpdateRuntimeFromTestArtifacts("crypto", cryptoRunDir, _logWatchdog, _logTest, _logRunner);

        var ibkrSuiteApiOk = await IsOkAsync("http://127.0.0.1:8110/health");
        var ibkrRunDir = GetIbkrShadowRunDir();
        var ibkrTestPid = FindTestProcessId(ibkrRunDir);
        var ibkrWatchdogPid = FindWatchdogProcessId(ibkrRunDir);
        var ibkrApiInfo = TryReadRuntimeInfo("ibkr", "api");
        var ibkrBacktestInfo = TryReadRuntimeInfo("ibkr", "backtest");
        _ibkrWatchdogProc = TryGetProcessById(ibkrWatchdogPid) ?? _ibkrWatchdogProc;
        var ibkrHeartbeatPath = Path.Combine(ibkrRunDir, "heartbeat.json");
        var ibkrWatchdogLogPath = Path.Combine(ibkrRunDir, "watchdog.log");
        var ibkrTestAgeSec = TryGetFileAgeSeconds(ibkrHeartbeatPath);
        var ibkrWatchdogAgeSec = TryGetFileAgeSeconds(ibkrWatchdogLogPath);
        UpdateRuntimeFromTestArtifacts("ibkr", ibkrRunDir, _logIbkrWatchdog, _logIbkrTest, _logIbkrRunner);
        var ibkrPortLabel = activeIbkrPort?.ToString() ?? configuredIbkrPort.ToString();
        var ibkrConfigSuffix = activeIbkrPort.HasValue && activeIbkrPort.Value != configuredIbkrPort
            ? $" | cfg {configuredIbkrPort}"
            : string.Empty;
        var ibkrStatusLine = $"IBKR {(ibkrOk ? "UP" : "DOWN")} | port {ibkrPortLabel} {(ibkrPortOk ? "LISTENING" : "OFF")}{ibkrConfigSuffix} | proc {(ibkrProcOk ? "RUNNING" : "OFF")} | mode {ibkrTradingMode}";
        if (!string.Equals(ibkrStatusLine, _lastIbkrStatusLine, StringComparison.Ordinal))
        {
            _lastIbkrStatusLine = ibkrStatusLine;
            AppendLog(_logIbkr, ibkrStatusLine);
        }

        var compactBot = "Bot status unknown";
        var tone = BadgeTone.Neutral;
        if (!string.IsNullOrWhiteSpace(botState))
        {
            var run = Regex.Match(botState, "\"run_id\"\\s*:\\s*\"([^\"]+)\"").Groups[1].Value;
            var reason = Regex.Match(botState, "\"stopped_reason\"\\s*:\\s*\"([^\"]+)\"").Groups[1].Value;
            var running = Regex.IsMatch(botState, "\"running\"\\s*:\\s*true", RegexOptions.IgnoreCase);
            if (running)
            {
                compactBot = $"Bot RUNNING | run {run}";
                tone = BadgeTone.Positive;
            }
            else
            {
                compactBot = $"Bot STOPPED | run {run}" + (string.IsNullOrWhiteSpace(reason) ? "" : $" | reason {reason}");
                tone = BadgeTone.Negative;
            }
        }
        SetBadge(_lblBot, compactBot, tone);
        SetBadge(_lblCollector, $"Collector {(IsAlive(_collectorProc) ? "RUNNING" : "STOPPED")}", IsAlive(_collectorProc) ? BadgeTone.Positive : BadgeTone.Negative);
        SetBadge(_lblNews, $"News worker {(newsWorker == true ? "RUNNING" : "IDLE/OFF")}", newsWorker == true ? BadgeTone.Positive : BadgeTone.Neutral);
        SetBadge(_lblMarketData, $"Market data worker {(marketDataWorker == true ? "RUNNING" : "IDLE/OFF")}", marketDataWorker == true ? BadgeTone.Positive : BadgeTone.Neutral);
        SetBadge(_lblMarketIntel, $"Market intel worker {(intelWorker == true ? "RUNNING" : "IDLE/OFF")}", intelWorker == true ? BadgeTone.Positive : BadgeTone.Neutral);
        SetBadge(_lblIbkr, $"IBKR {(ibkrOk ? "UP" : "DOWN")} | port {ibkrPortLabel} {(ibkrPortOk ? "LISTENING" : "OFF")}", ibkrOk ? BadgeTone.Positive : BadgeTone.Negative);
        var watchdogOk = watchdogPid.HasValue || (watchdogAgeSec.HasValue && watchdogAgeSec.Value <= 600);
        var ibkrWatchdogOk = ibkrWatchdogPid.HasValue || (ibkrWatchdogAgeSec.HasValue && ibkrWatchdogAgeSec.Value <= 600);
        SetBadge(_lblTest, testPid.HasValue ? $"TEST RUNNING | {FormatAge(testAgeSec)}" : "TEST STOPPED | click", testPid.HasValue ? BadgeTone.Positive : BadgeTone.Negative);
        SetBadge(_lblWatchdog, watchdogOk ? $"WATCHDOG RUNNING | {FormatAge(watchdogAgeSec)}" : "WATCHDOG STOPPED | click", watchdogOk ? BadgeTone.Positive : BadgeTone.Negative);
        _btnProjectAction.Text = IsProjectRunning() ? "Restart Project" : "Start Project";
        UpdateRuntimeDiagnostics("main_api", "MAIN API", apiOk, mainApiInfo, $"port={_txtApiPort.Text.Trim()}");
        UpdateRuntimeDiagnostics("main_backtest", "MAIN BACKTEST", backtestOk, mainBacktestInfo, $"port={_txtBacktestPort.Text.Trim()}");
        UpdateRuntimeDiagnostics("main_collector", "MAIN COLLECTOR", IsAlive(_collectorProc), mainCollectorInfo, $"finder={FormatRuntimePid(collectorPid)}");
        UpdateRuntimeDiagnostics("main_dashboard", "MAIN DASHBOARD", dashOk, mainDashboardInfo, $"port={_txtDashPort.Text.Trim()}");
        UpdateRuntimeDiagnostics("ibkr_api", "IBKR API", ibkrSuiteApiOk, ibkrApiInfo, "port=8110");
        UpdateRuntimeDiagnostics("ibkr_backtest", "IBKR BACKTEST", await IsOkAsync("http://127.0.0.1:8101/health"), ibkrBacktestInfo, "port=8101");
        await RefreshPortDetailsAsync(logResult: false);

        SetTabState("Dashboard", ResolveEndpointState(_dashboardProc, dashOk));
        SetTabState("Dashboard Log", ResolveEndpointState(_dashboardProc, dashOk));
        SetTabState("Auto Tune Log", ResolveEndpointState(_apiProc, apiOk));
        SetTabState("Shadow Report", ResolveEndpointState(_apiProc, apiOk));
        SetTabState("Traded Symbols", ResolveEndpointState(_apiProc, apiOk));
        SetTabState("IBKR", ibkrOk ? TabStateTone.Up : TabStateTone.Error);
        SetTabState("TEST", testPid.HasValue ? TabStateTone.Up : TabStateTone.Error);
        SetTabState("WATCHDOG", watchdogOk ? TabStateTone.Up : TabStateTone.Error);
        SetTabState("RUNNER", testPid.HasValue ? TabStateTone.Up : TabStateTone.Error);
        SetTabState("IBKR TEST", ibkrTestPid.HasValue ? TabStateTone.Up : (ibkrSuiteApiOk ? TabStateTone.Waiting : TabStateTone.Error));
        SetTabState("IBKR WATCHDOG", ibkrWatchdogOk ? TabStateTone.Up : TabStateTone.Error);
        SetTabState("IBKR RUNNER", ibkrTestPid.HasValue ? TabStateTone.Up : (ibkrSuiteApiOk ? TabStateTone.Waiting : TabStateTone.Error));
        SetTabLabel("IBKR", ibkrOk ? "IBKR UP" : "IBKR DOWN");
        SetTabLabel("TEST", testPid.HasValue ? $"TEST {FormatAge(testAgeSec)}" : "TEST STOPPED");
        SetTabLabel("WATCHDOG", watchdogOk ? $"WATCHDOG {FormatAge(watchdogAgeSec)}" : "WATCHDOG STOPPED");
        SetTabLabel("RUNNER", testPid.HasValue ? $"RUNNER {FormatAge(testAgeSec)}" : "RUNNER STOPPED");
        SetTabLabel("IBKR TEST", ibkrTestPid.HasValue ? $"IBKR TEST {FormatAge(ibkrTestAgeSec)}" : (ibkrSuiteApiOk ? "IBKR TEST WAIT" : "IBKR TEST STOPPED"));
        SetTabLabel("IBKR WATCHDOG", ibkrWatchdogOk ? $"IBKR WATCHDOG {FormatAge(ibkrWatchdogAgeSec)}" : "IBKR WATCHDOG STOPPED");
        SetTabLabel("IBKR RUNNER", ibkrTestPid.HasValue ? $"IBKR RUNNER {FormatAge(ibkrTestAgeSec)}" : (ibkrSuiteApiOk ? "IBKR RUNNER WAIT" : "IBKR RUNNER STOPPED"));
        SetTabState("Control", ResolveControlState());
        SetTabState("Launcher Log", ResolveControlState());
        RefreshTabButtonStyles();
    }

    private async Task UpdateTradedSymbolsLogAsync(bool force)
    {
        try
        {
            var runsPayload = await TryGetAsync($"{ApiBase()}/bot/runs");
            if (string.IsNullOrWhiteSpace(runsPayload))
            {
                if (force) SetRichTextBoxTextSafely(_logTradedSymbols, "Waiting for API: /bot/runs not available yet.", moveCaretToStart: true);
                return;
            }

            using var runsDoc = JsonDocument.Parse(runsPayload);
            JsonElement runsRoot = runsDoc.RootElement;
            if (runsRoot.ValueKind == JsonValueKind.Object && runsRoot.TryGetProperty("value", out var arr) && arr.ValueKind == JsonValueKind.Array)
            {
                runsRoot = arr;
            }
            if (runsRoot.ValueKind != JsonValueKind.Array)
            {
                if (force) SetRichTextBoxTextSafely(_logTradedSymbols, "Waiting for API: invalid /bot/runs payload.", moveCaretToStart: true);
                return;
            }

            var all = new Dictionary<string, (string Asset, int ClosedTrades, bool InShadowOrPolicySet, bool InSignalSet, string LastUtc)>(StringComparer.OrdinalIgnoreCase);
            var runCount = 0;
            foreach (var run in runsRoot.EnumerateArray())
            {
                if (!run.TryGetProperty("run_id", out var ridProp)) continue;
                var runId = ridProp.GetString();
                if (string.IsNullOrWhiteSpace(runId)) continue;
                runCount++;

                var closedPayload = await TryGetAsync($"{ApiBase()}/bot/positions/closed?run_id={Uri.EscapeDataString(runId)}&limit=1000");
                if (string.IsNullOrWhiteSpace(closedPayload)) continue;

                using var closedDoc = JsonDocument.Parse(closedPayload);
                if (closedDoc.RootElement.ValueKind != JsonValueKind.Array) continue;
                foreach (var pos in closedDoc.RootElement.EnumerateArray())
                {
                    if (!pos.TryGetProperty("symbol", out var symProp)) continue;
                    var symbol = (symProp.GetString() ?? string.Empty).Trim();
                    if (string.IsNullOrWhiteSpace(symbol)) continue;
                    var asset = symbol.Contains("/") ? symbol.Split('/')[0].ToUpperInvariant() : symbol.ToUpperInvariant();
                    var exitTime = pos.TryGetProperty("exit_time", out var et) ? (et.GetString() ?? string.Empty) : string.Empty;

                    if (!all.TryGetValue(symbol, out var row))
                    {
                        all[symbol] = (asset, 1, false, false, exitTime);
                    }
                    else
                    {
                        var newer = string.CompareOrdinal(exitTime, row.LastUtc) > 0 ? exitTime : row.LastUtc;
                        all[symbol] = (row.Asset, row.ClosedTrades + 1, row.InShadowOrPolicySet, row.InSignalSet, newer);
                    }
                }

                // Use authoritative unique symbol source for paper/shadow/signal sets.
                var tradedPayload = await TryGetAsync($"{ApiBase()}/bot/traded-symbols?run_id={Uri.EscapeDataString(runId)}&lookback_hours=8760");
                if (string.IsNullOrWhiteSpace(tradedPayload)) continue;
                using var tradedDoc = JsonDocument.Parse(tradedPayload);
                var trRoot = tradedDoc.RootElement;
                if (trRoot.ValueKind == JsonValueKind.Object)
                {
                    if (trRoot.TryGetProperty("shadow_symbols", out var shadowSyms) && shadowSyms.ValueKind == JsonValueKind.Array)
                    {
                        foreach (var s in shadowSyms.EnumerateArray())
                        {
                            var symbol = (s.GetString() ?? string.Empty).Trim();
                            if (string.IsNullOrWhiteSpace(symbol)) continue;
                            var asset = symbol.Contains("/") ? symbol.Split('/')[0].ToUpperInvariant() : symbol.ToUpperInvariant();
                            if (!all.TryGetValue(symbol, out var row))
                                row = (asset, 0, false, false, "");
                            all[symbol] = (row.Asset, row.ClosedTrades, true, row.InSignalSet, row.LastUtc);
                        }
                    }
                    if (trRoot.TryGetProperty("signal_symbols", out var signalSyms) && signalSyms.ValueKind == JsonValueKind.Array)
                    {
                        foreach (var s in signalSyms.EnumerateArray())
                        {
                            var symbol = (s.GetString() ?? string.Empty).Trim();
                            if (string.IsNullOrWhiteSpace(symbol)) continue;
                            var asset = symbol.Contains("/") ? symbol.Split('/')[0].ToUpperInvariant() : symbol.ToUpperInvariant();
                            if (!all.TryGetValue(symbol, out var row))
                                row = (asset, 0, false, false, "");
                            all[symbol] = (row.Asset, row.ClosedTrades, row.InShadowOrPolicySet, true, row.LastUtc);
                        }
                    }
                    if (trRoot.TryGetProperty("cross_asset_symbols", out var crossSyms) && crossSyms.ValueKind == JsonValueKind.Array)
                    {
                        foreach (var s in crossSyms.EnumerateArray())
                        {
                            var symbol = (s.GetString() ?? string.Empty).Trim();
                            if (string.IsNullOrWhiteSpace(symbol)) continue;
                            var asset = symbol.Contains("/") ? symbol.Split('/')[0].ToUpperInvariant() : symbol.ToUpperInvariant();
                            if (!all.TryGetValue(symbol, out var row))
                                row = (asset, 0, false, false, "");
                            // Cross-asset symbols are part of tracked symbol universe in this tab.
                            all[symbol] = (row.Asset, row.ClosedTrades, row.InShadowOrPolicySet, true, row.LastUtc);
                        }
                    }
                }
            }

            string shadowSummaryLine = "";
            try
            {
                var sr = await TryGetAsync($"{ApiBase()}/bot/signal-quality/shadow-report?lookback_hours=720&horizon_min=120&limit=10000&actions=shadow,policy,executed");
                if (!string.IsNullOrWhiteSpace(sr))
                {
                    using var srDoc = JsonDocument.Parse(sr);
                    var root = srDoc.RootElement;
                    var cnt = root.TryGetProperty("counts", out var c) ? c : default;
                    var sum = root.TryGetProperty("summary", out var s) ? s : default;
                    var sh = cnt.ValueKind != JsonValueKind.Undefined && cnt.TryGetProperty("shadow", out var shv) ? shv.ToString() : "?";
                    var pol = cnt.ValueKind != JsonValueKind.Undefined && cnt.TryGetProperty("policy", out var pv) ? pv.ToString() : "?";
                    var ex = cnt.ValueKind != JsonValueKind.Undefined && cnt.TryGetProperty("executed", out var ev) ? ev.ToString() : "?";
                    var eval = sum.ValueKind != JsonValueKind.Undefined && sum.TryGetProperty("shadow_eval_samples", out var sev) ? sev.ToString() : "?";
                    shadowSummaryLine = $"shadow-report(720h): shadow={sh}, policy={pol}, executed={ex}, eval_samples={eval}";
                }
            }
            catch { }

            var symbolWidth = Math.Max("SYMBOL".Length, all.Count == 0 ? 0 : all.Keys.Max(x => x.Length));
            var assetWidth = Math.Max("ASSET".Length, all.Count == 0 ? 0 : all.Values.Max(x => (x.Asset ?? string.Empty).Length));
            var closedWidth = Math.Max("CLOSED_TRADES".Length, all.Count == 0 ? 0 : all.Values.Max(x => x.ClosedTrades.ToString().Length));
            const int inShadowWidth = 19;
            const int inSignalWidth = 13;

            string FmtRow(string symbol, string asset, string closedTrades, string inShadow, string inSignal, string lastUtc)
            {
                return string.Join("  ", new[]
                {
                    symbol.PadRight(symbolWidth),
                    asset.PadRight(assetWidth),
                    closedTrades.PadLeft(closedWidth),
                    inShadow.PadLeft(inShadowWidth),
                    inSignal.PadLeft(inSignalWidth),
                    lastUtc ?? string.Empty
                });
            }

            var lines = new List<string>
            {
                $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] Unique symbols (real/paper closed + shadow/policy signal sets, no duplicates)",
                $"runs scanned: {runCount}",
                $"unique symbols: {all.Count}",
                string.IsNullOrWhiteSpace(shadowSummaryLine) ? "shadow-report(720h): unavailable" : shadowSummaryLine,
                "",
                FmtRow("SYMBOL", "ASSET", "CLOSED_TRADES", "IN_SHADOW_OR_POLICY", "IN_ANY_SIGNAL", "LAST_EVENT_UTC")
            };
            foreach (var kv in all.OrderBy(k => k.Key, StringComparer.OrdinalIgnoreCase))
            {
                lines.Add(
                    FmtRow(
                        kv.Key,
                        kv.Value.Asset,
                        kv.Value.ClosedTrades.ToString(),
                        kv.Value.InShadowOrPolicySet ? "1" : "0",
                        kv.Value.InSignalSet ? "1" : "0",
                        kv.Value.LastUtc
                    )
                );
            }
            if (all.Count == 0)
            {
                lines.Add("(no symbols found in closed trades/signals in scanned runs)");
            }

            var text = string.Join(Environment.NewLine, lines);
            SetRichTextBoxTextSafely(_logTradedSymbols, text, moveCaretToStart: true);
        }
        catch (Exception ex)
        {
            if (force)
            {
                SetRichTextBoxTextSafely(_logTradedSymbols, $"Load failed: {ex.Message}", moveCaretToStart: true);
            }
            AppendLog(_logLauncher, $"Traded symbols refresh failed: {ex.Message}");
        }
    }

    private void UpdateAutoTuneLog(string? payload)
    {
        if (string.IsNullOrWhiteSpace(payload)) return;
        try
        {
            using var doc = JsonDocument.Parse(payload);
            if (!doc.RootElement.TryGetProperty("latest", out var latest)) return;
            if (latest.ValueKind == JsonValueKind.Null) return;
            if (!latest.TryGetProperty("created_at", out var createdProp)) return;
            var createdAt = createdProp.GetString();
            if (string.IsNullOrWhiteSpace(createdAt)) return;
            if (string.Equals(createdAt, _lastAutoTuneCreatedAt, StringComparison.Ordinal)) return;
            _lastAutoTuneCreatedAt = createdAt;

            var guard = latest.TryGetProperty("apply_guard_passed", out var guardProp) && guardProp.ValueKind == JsonValueKind.True;
            AppendLog(_logAutoTune, $"Recommendation created_at={createdAt} | guard={(guard ? "PASS" : "FAIL")}");

            if (latest.TryGetProperty("selected", out var selected) && selected.ValueKind == JsonValueKind.Object)
            {
                if (selected.TryGetProperty("summary", out var summary) && summary.ValueKind == JsonValueKind.Object)
                {
                    var wr = summary.TryGetProperty("win_rate", out var wrP) ? wrP.ToString() : "?";
                    var pf = summary.TryGetProperty("profit_factor", out var pfP) ? pfP.ToString() : "?";
                    var eq = summary.TryGetProperty("final_equity", out var eqP) ? eqP.ToString() : "?";
                    var tr = summary.TryGetProperty("total_trades", out var trP) ? trP.ToString() : "?";
                    AppendLog(_logAutoTune, $"Summary WR={wr} PF={pf} Equity={eq} Trades={tr}");
                }
                if (selected.TryGetProperty("overrides", out var overrides) && overrides.ValueKind == JsonValueKind.Object)
                {
                    var parts = new List<string>();
                    foreach (var p in overrides.EnumerateObject())
                        parts.Add($"{p.Name}={p.Value}");
                    AppendLog(_logAutoTune, $"Overrides: {string.Join(", ", parts)}");
                }
            }
        }
        catch (Exception ex)
        {
            AppendLog(_logAutoTune, $"Auto-tune payload parse failed: {ex.Message}");
        }
    }

    private void UpdateShadowReportLog(string? payload)
    {
        if (string.IsNullOrWhiteSpace(payload))
            return;
        if (IsDisposed || Disposing)
            return;
        if (_logShadowReport.InvokeRequired)
        {
            try
            {
                if (!IsControlUsable(_logShadowReport))
                    return;
                _logShadowReport.BeginInvoke(new Action(() => UpdateShadowReportLog(payload)));
            }
            catch
            {
            }
            return;
        }
        if (!IsControlUsable(_logShadowReport))
            return;
        if (string.Equals(payload, _lastShadowReportPayload, StringComparison.Ordinal))
            return;
        _lastShadowReportPayload = payload;
        string pretty = payload;
        try
        {
            using var doc = JsonDocument.Parse(payload);
            var root = doc.RootElement;
            var runId = root.TryGetProperty("run_id", out var run) ? run.ToString() : "?";
            var win = root.TryGetProperty("window", out var w) ? w : default;
            var cnt = root.TryGetProperty("counts", out var c) ? c : default;
            var sum = root.TryGetProperty("summary", out var s) ? s : default;
            var lookback = win.ValueKind != JsonValueKind.Undefined && win.TryGetProperty("lookback_hours", out var lbh) ? lbh.ToString() : "?";
            var horizon = win.ValueKind != JsonValueKind.Undefined && win.TryGetProperty("horizon_min", out var hm) ? hm.ToString() : "?";
            var total = cnt.ValueKind != JsonValueKind.Undefined && cnt.TryGetProperty("total", out var t) ? t.ToString() : "?";
            var policy = cnt.ValueKind != JsonValueKind.Undefined && cnt.TryGetProperty("policy", out var p) ? p.ToString() : "?";
            var blocked = cnt.ValueKind != JsonValueKind.Undefined && cnt.TryGetProperty("blocked", out var b) ? b.ToString() : "?";
            var shadow = cnt.ValueKind != JsonValueKind.Undefined && cnt.TryGetProperty("shadow", out var sh) ? sh.ToString() : "?";
            var executed = cnt.ValueKind != JsonValueKind.Undefined && cnt.TryGetProperty("executed", out var ex) ? ex.ToString() : "?";
            var eval = sum.ValueKind != JsonValueKind.Undefined && sum.TryGetProperty("shadow_eval_samples", out var se) ? se.ToString() : "?";
            var wr = sum.ValueKind != JsonValueKind.Undefined && sum.TryGetProperty("shadow_win_rate_h", out var swr) ? swr.ToString() : "?";
            var pf = sum.ValueKind != JsonValueKind.Undefined && sum.TryGetProperty("shadow_profit_factor_h", out var spf) ? spf.ToString() : "?";
            var avg = sum.ValueKind != JsonValueKind.Undefined && sum.TryGetProperty("shadow_avg_ret_h", out var sar) ? sar.ToString() : "?";
            pretty =
                $"run_id: {runId}\r\n" +
                $"window: {lookback}h / horizon {horizon}m\r\n\r\n" +
                $"counts: total={total}, policy={policy}, blocked={blocked}, shadow={shadow}, executed={executed}\r\n" +
                $"summary: eval_samples={eval}, win_rate_h={wr}, pf_h={pf}, avg_ret_h={avg}\r\n\r\n" +
                $"(compact view; full JSON is available via API endpoint)";
        }
        catch
        {
        }
        SetRichTextBoxTextSafely(
            _logShadowReport,
            $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] Latest shadow report (24h, actions={_shadowActionsParam})\r\n\r\n{pretty}",
            moveCaretToStart: true
        );
    }

    private async Task RefreshPortDetailsAsync(bool logResult)
    {
        await Task.Yield();
        UpdatePortBadge(_lblApiPort, "API", _txtApiPort.Text.Trim(), _apiProc?.Id, logResult);
        UpdatePortBadge(_lblBacktestPort, "Backtest", _txtBacktestPort.Text.Trim(), _backtestProc?.Id, logResult);
        UpdatePortBadge(_lblDashPort, "Dashboard", _txtDashPort.Text.Trim(), _dashboardProc?.Id, logResult);
    }

    private void UpdatePortBadge(BadgeLabel badge, string serviceName, string portText, int? expectedPid, bool logResult)
    {
        if (!int.TryParse(portText, out var port))
        {
            SetBadge(badge, $"{serviceName} port {portText}: invalid port", BadgeTone.Negative);
            return;
        }

        var pids = new HashSet<int>(GetPidsFromNetstat(port));
        if (pids.Count == 0)
        {
            if (IsPortListeningLocal(port))
            {
                var msgUnknown = $"{serviceName} port {port}: LISTENING (owner unresolved)";
                SetBadge(badge, msgUnknown, BadgeTone.Positive);
                if (logResult) AppendLog(_logLauncher, msgUnknown);
                return;
            }
            SetBadge(badge, $"{serviceName} port {port}: FREE", BadgeTone.Neutral);
            if (logResult) AppendLog(_logLauncher, $"{serviceName} port {port} is FREE.");
            return;
        }

        var pidList = string.Join(",", pids);
        var owner = ResolvePidOwner(pids);
        var expectedRunning = expectedPid.HasValue && pids.Contains(expectedPid.Value);
        var expectedByName = IsExpectedOwnerByService(serviceName, owner);
        var tone = (expectedRunning || expectedByName) ? BadgeTone.Positive : BadgeTone.Negative;
        var msg = $"{serviceName} port {port}: LISTENING pid {pidList} ({owner})";
        SetBadge(badge, msg, tone);
        if (logResult) AppendLog(_logLauncher, msg);
    }

    private static string ResolvePidOwner(IEnumerable<int> pids)
    {
        foreach (var pid in pids)
        {
            try
            {
                var proc = Process.GetProcessById(pid);
                return proc.ProcessName;
            }
            catch { }
        }
        return "unknown";
    }

    private static bool IsExpectedOwnerByService(string serviceName, string owner)
    {
        var s = (serviceName ?? "").ToLowerInvariant();
        var o = (owner ?? "").ToLowerInvariant();
        if (s.Contains("dashboard")) return o.Contains("node") || o.Contains("npm") || o.Contains("vite");
        if (s.Contains("api") || s.Contains("backtest")) return o.Contains("python");
        return false;
    }

    private static bool? ParseBoolField(string? payload, string key)
    {
        if (string.IsNullOrWhiteSpace(payload)) return null;
        var m = Regex.Match(payload, $"\"{Regex.Escape(key)}\"\\s*:\\s*(true|false)", RegexOptions.IgnoreCase);
        if (!m.Success) return null;
        return string.Equals(m.Groups[1].Value, "true", StringComparison.OrdinalIgnoreCase);
    }

    private static TabStateTone ResolveProcessState(Process? proc, bool healthy, bool hasHealthCheck)
    {
        if (proc == null) return TabStateTone.Off;
        bool isAlive;
        try
        {
            isAlive = !proc.HasExited;
        }
        catch
        {
            return TabStateTone.Off;
        }
        if (!isAlive) return TabStateTone.Error;
        if (!hasHealthCheck) return TabStateTone.Up;
        return healthy ? TabStateTone.Up : TabStateTone.Waiting;
    }

    private static TabStateTone ResolveEndpointState(Process? proc, bool healthy)
    {
        if (healthy) return TabStateTone.Up;
        if (proc == null) return TabStateTone.Off;
        bool isAlive;
        try
        {
            isAlive = !proc.HasExited;
        }
        catch
        {
            return TabStateTone.Off;
        }
        return isAlive ? TabStateTone.Waiting : TabStateTone.Error;
    }

    private static TabStateTone ResolveWorkerState(Process? proc, bool? workerEnabled)
    {
        if (proc == null)
        {
            if (workerEnabled.HasValue)
                return workerEnabled.Value ? TabStateTone.Up : TabStateTone.Waiting;
            return TabStateTone.Off;
        }
        bool isAlive;
        try
        {
            isAlive = !proc.HasExited;
        }
        catch
        {
            return TabStateTone.Off;
        }
        if (!isAlive) return TabStateTone.Error;
        if (workerEnabled.HasValue && workerEnabled.Value == false) return TabStateTone.Waiting;
        return TabStateTone.Up;
    }

    private TabStateTone ResolveControlState()
    {
        var anyAlive =
            IsAlive(_apiProc) ||
            IsAlive(_backtestProc) ||
            IsAlive(_collectorProc) ||
            IsAlive(_newsProc) ||
            IsAlive(_marketDataProc) ||
            IsAlive(_marketIntelProc) ||
            IsAlive(_dashboardProc);
        return anyAlive ? TabStateTone.Waiting : TabStateTone.Off;
    }

    private static bool IsAlive(Process? proc)
    {
        if (proc == null) return false;
        try
        {
            return !proc.HasExited;
        }
        catch
        {
            return false;
        }
    }

    private static void SetBadge(BadgeLabel label, string text, BadgeTone tone)
    {
        label.Text = text;
        switch (tone)
        {
            case BadgeTone.Positive:
                label.BackColor = Color.FromArgb(8, 48, 30);
                label.ForeColor = Color.FromArgb(134, 239, 172);
                label.BorderColor = Color.FromArgb(22, 101, 52);
                break;
            case BadgeTone.Negative:
                label.BackColor = Color.FromArgb(66, 16, 18);
                label.ForeColor = Color.FromArgb(254, 202, 202);
                label.BorderColor = Color.FromArgb(127, 29, 29);
                break;
            default:
                label.BackColor = Color.FromArgb(62, 45, 13);
                label.ForeColor = Color.FromArgb(253, 230, 138);
                label.BorderColor = Color.FromArgb(120, 53, 15);
                break;
        }
        label.Invalidate();
    }

    private async Task<bool> IsOkAsync(string url)
    {
        var isHealth = url.IndexOf("/health", StringComparison.OrdinalIgnoreCase) >= 0;
        var attempts = isHealth ? 3 : 2;
        var perTryTimeoutSec = isHealth ? 6 : 4;
        for (var i = 0; i < attempts; i++)
        {
            try
            {
                using var cts = new System.Threading.CancellationTokenSource(TimeSpan.FromSeconds(perTryTimeoutSec));
                using var res = await _http.GetAsync(url, cts.Token);
                if (res.IsSuccessStatusCode) return true;
            }
            catch
            {
            }
            await Task.Delay(250);
        }
        if (isHealth && TryGetLocalPortFromUrl(url, out var port) && IsPortListeningLocal(port))
        {
            // Fallback: local API listener is up, /health can be transiently slow under load.
            return true;
        }
        return false;
    }

    private async Task<bool> IsStrictOkAsync(string url, int timeoutSec = 6)
    {
        try
        {
            using var cts = new System.Threading.CancellationTokenSource(TimeSpan.FromSeconds(Math.Max(1, timeoutSec)));
            using var res = await _http.GetAsync(url, cts.Token);
            return res.IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }

    private static bool TryGetLocalPortFromUrl(string url, out int port)
    {
        port = 0;
        try
        {
            var uri = new Uri(url);
            if (uri.Port <= 0) return false;
            var host = (uri.Host ?? string.Empty).Trim();
            if (!host.Equals("localhost", StringComparison.OrdinalIgnoreCase) &&
                !host.Equals("127.0.0.1", StringComparison.OrdinalIgnoreCase) &&
                !host.Equals("::1", StringComparison.OrdinalIgnoreCase))
                return false;
            port = uri.Port;
            return true;
        }
        catch
        {
            return false;
        }
    }

    private static bool IsPortListeningLocal(int port)
    {
        try
        {
            var listeners = IPGlobalProperties.GetIPGlobalProperties().GetActiveTcpListeners();
            for (var i = 0; i < listeners.Length; i++)
            {
                var ep = listeners[i];
                if (ep.Port != port) continue;
                if (IPAddress.IsLoopback(ep.Address) || ep.Address.Equals(IPAddress.Any) || ep.Address.Equals(IPAddress.IPv6Any))
                    return true;
            }
        }
        catch
        {
        }
        return false;
    }

    private async Task<string?> TryGetAsync(string url)
    {
        try
        {
            var s = await _http.GetStringAsync(url);
            return s;
        }
        catch
        {
            return null;
        }
    }

    private static void StyleButton(Button b, Color bg)
    {
        var baseColor = Color.FromArgb(54, 58, 66);
        var hoverColor = Color.FromArgb(70, 75, 84);
        b.BackColor = baseColor;
        b.ForeColor = Color.FromArgb(226, 232, 240);
        b.FlatStyle = FlatStyle.Flat;
        b.FlatAppearance.BorderSize = 1;
        b.FlatAppearance.BorderColor = Color.FromArgb(95, 102, 115);
        b.Height = 32;
        b.Cursor = Cursors.Hand;
        b.MouseEnter += (_, __) => b.BackColor = hoverColor;
        b.MouseLeave += (_, __) => b.BackColor = baseColor;
        b.Resize += (_, __) =>
        {
            var path = RoundedRect(new Rectangle(0, 0, b.Width, b.Height), 8);
            b.Region = new Region(path);
        };
        var init = RoundedRect(new Rectangle(0, 0, b.Width, b.Height), 8);
        b.Region = new Region(init);
    }

    private static void ApplyTheme(Control root)
    {
        foreach (Control c in root.Controls)
        {
            if (c is GroupBox g)
            {
                g.BackColor = PanelBg;
                g.ForeColor = Fg;
            }
            else if (c is FlowLayoutPanel f)
            {
                f.BackColor = Color.Transparent;
                f.ForeColor = Fg;
            }
            else if (c is Label l)
            {
                l.ForeColor = Muted;
                l.BackColor = Color.Transparent;
            }
            else if (c is TextBox t)
            {
                t.BackColor = InputFill;
                t.ForeColor = Color.FromArgb(241, 245, 249);
                t.BorderStyle = BorderStyle.None;
            }
            else if (c is CheckBox cb)
            {
                cb.ForeColor = Fg;
                cb.BackColor = Color.Transparent;
            }
            else if (c is TabControl tc)
            {
                tc.BackColor = Bg;
                tc.ForeColor = Fg;
            }
            else if (c is TabPage tp)
            {
                tp.BackColor = Bg;
                tp.ForeColor = Fg;
            }

            if (c.Controls.Count > 0) ApplyTheme(c);
        }
    }

    private static GraphicsPath RoundedRect(Rectangle r, int radius)
    {
        var d = radius * 2;
        var path = new GraphicsPath();
        path.StartFigure();
        path.AddArc(r.X, r.Y, d, d, 180, 90);
        path.AddArc(r.Right - d, r.Y, d, d, 270, 90);
        path.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
        path.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
        path.CloseFigure();
        return path;
    }

    private static void DrawTab(TabControl tabs, DrawItemEventArgs e)
    {
        var g = e.Graphics;
        var r = e.Bounds;
        var selected = (e.State & DrawItemState.Selected) == DrawItemState.Selected;
        var bg = selected ? Color.FromArgb(58, 63, 72) : Color.FromArgb(30, 34, 41);
        using var b = new SolidBrush(bg);
        using var p = new Pen(Color.FromArgb(77, 84, 96));
        g.FillRectangle(b, r);
        g.DrawRectangle(p, r.X, r.Y, r.Width - 1, r.Height - 1);
        var text = tabs.TabPages[e.Index].Text;
        TextRenderer.DrawText(
            g,
            text,
            tabs.Font,
            r,
            selected ? Color.White : Color.FromArgb(226, 232, 240),
            TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis
        );
    }

    private Panel BuildTabNav(IReadOnlyList<(string Title, Control Content)> pages, Action<int> onSelect)
    {
        var wrap = new Panel
        {
            Dock = DockStyle.Top,
            Height = 72,
            BackColor = Color.FromArgb(20, 23, 29),
            Padding = new Padding(6, 6, 6, 6)
        };
        var row = new FlowLayoutPanel
        {
            Dock = DockStyle.Fill,
            WrapContents = true,
            AutoScroll = false,
            FlowDirection = FlowDirection.LeftToRight,
            BackColor = Color.Transparent,
            Margin = new Padding(0),
            Padding = new Padding(0)
        };
        wrap.Controls.Add(row);

        var selectedIndex = 0;
        _tabButtons.Clear();
        _tabStates.Clear();
        for (int i = 0; i < pages.Count; i++)
        {
            var idx = i;
            var title = pages[i].Title;
            var b = new Button
            {
                Text = title,
                Width = 122,
                Height = 24,
                FlatStyle = FlatStyle.Flat,
                BackColor = Color.FromArgb(30, 34, 41),
                ForeColor = Color.FromArgb(226, 232, 240),
                Margin = new Padding(0, 0, 6, 0),
                Cursor = Cursors.Hand
            };
            b.FlatAppearance.BorderColor = Color.FromArgb(77, 84, 96);
            b.FlatAppearance.BorderSize = 1;
            b.Click += (_, __) =>
            {
                try
                {
                    selectedIndex = idx;
                    _activeTabTitle = title;
                    onSelect(idx);
                    RefreshTabButtonStyles();
                }
                catch (Exception ex)
                {
                    AppendLog(_logLauncher, $"Tab click error ({title}): {ex.Message}");
                }
            };
            row.Controls.Add(b);
            _tabButtons[title] = b;
            _tabStates[title] = TabStateTone.Off;
        }
        _activeTabTitle = pages.Count > 0 ? pages[0].Title : "Control";
        RefreshTabButtonStyles();

        var grad = new Panel { Dock = DockStyle.Bottom, Height = 3 };
        grad.Paint += (_, e) =>
        {
            using var lg = new LinearGradientBrush(new Rectangle(0, 0, grad.Width, grad.Height), Color.FromArgb(58, 63, 72), Color.FromArgb(20, 23, 29), LinearGradientMode.Vertical);
            e.Graphics.FillRectangle(lg, 0, 0, grad.Width, grad.Height);
        };
        wrap.Controls.Add(grad);
        return wrap;
    }

    private void SetTabState(string title, TabStateTone state)
    {
        _tabStates[title] = state;
    }

    private void SetTabLabel(string title, string text)
    {
        if (_tabButtons.TryGetValue(title, out var button))
            button.Text = text;
    }

    private void RefreshTabButtonStyles()
    {
        try
        {
            foreach (var kv in _tabButtons)
            {
                var title = kv.Key;
                var button = kv.Value;
                var isActive = string.Equals(title, _activeTabTitle, StringComparison.Ordinal);
                _tabStates.TryGetValue(title, out var state);
                ApplyTabButtonTheme(button, state, isActive);
            }
        }
        catch
        {
            // Never fail UI refresh due to transient control/process state.
        }
    }

    private static void ApplyTabButtonTheme(Button b, TabStateTone state, bool isActive)
    {
        Color back;
        Color border;
        Color fore;
        switch (state)
        {
            case TabStateTone.Up:
                back = Color.FromArgb(16, 64, 38);
                border = Color.FromArgb(34, 197, 94);
                fore = Color.FromArgb(220, 252, 231);
                break;
            case TabStateTone.Waiting:
                back = Color.FromArgb(86, 58, 18);
                border = Color.FromArgb(245, 158, 11);
                fore = Color.FromArgb(254, 243, 199);
                break;
            case TabStateTone.Error:
                back = Color.FromArgb(90, 28, 28);
                border = Color.FromArgb(239, 68, 68);
                fore = Color.FromArgb(254, 226, 226);
                break;
            default:
                back = Color.FromArgb(42, 46, 54);
                border = Color.FromArgb(95, 102, 115);
                fore = Color.FromArgb(226, 232, 240);
                break;
        }

        if (isActive)
        {
            back = ControlPaint.Light(back, 0.08f);
            border = ControlPaint.Light(border, 0.12f);
        }

        b.BackColor = back;
        b.FlatAppearance.BorderColor = border;
        b.ForeColor = fore;
    }

    private Panel BuildTitleBar()
    {
        var titleBar = new Panel
        {
            Dock = DockStyle.Top,
            Height = 34,
            BackColor = Color.FromArgb(22, 25, 31)
        };
        var title = new Label
        {
            Text = "AIInvest Launcher",
            ForeColor = Color.FromArgb(226, 232, 240),
            AutoSize = true,
            Location = new Point(10, 9)
        };
        var btnClose = BuildTitleButton("X", Color.FromArgb(153, 27, 27), (_, __) => Close());
        var btnMax = BuildTitleButton("▢", Color.FromArgb(71, 85, 105), (_, __) => TogglePseudoMaximize());
        var btnMin = BuildTitleButton("—", Color.FromArgb(55, 65, 81), (_, __) => WindowState = FormWindowState.Minimized);

        btnClose.Left = Width - 44;
        btnMax.Left = Width - 88;
        btnMin.Left = Width - 132;
        titleBar.Controls.Add(title);
        titleBar.Controls.Add(btnClose);
        titleBar.Controls.Add(btnMax);
        titleBar.Controls.Add(btnMin);
        const int WM_NCLBUTTONDOWN = 0xA1;
        const int HTCAPTION_DRAG = 2;
        void BeginNativeDrag()
        {
            try
            {
                ReleaseCapture();
                SendMessage(Handle, WM_NCLBUTTONDOWN, HTCAPTION_DRAG, 0);
            }
            catch { }
        }
        titleBar.MouseDown += (_, e) =>
        {
            if (e.Button == MouseButtons.Left && e.Clicks == 1) BeginNativeDrag();
        };
        title.MouseDown += (_, e) =>
        {
            if (e.Button == MouseButtons.Left && e.Clicks == 1) BeginNativeDrag();
        };
        titleBar.MouseDoubleClick += (_, e) =>
        {
            if (e.Button == MouseButtons.Left) TogglePseudoMaximize();
        };
        title.MouseDoubleClick += (_, e) =>
        {
            if (e.Button == MouseButtons.Left) TogglePseudoMaximize();
        };
        titleBar.Resize += (_, __) =>
        {
            btnClose.Left = titleBar.Width - 40;
            btnMax.Left = titleBar.Width - 80;
            btnMin.Left = titleBar.Width - 120;
        };
        return titleBar;
    }

    private static Button BuildTitleButton(string text, Color bg, EventHandler onClick)
    {
        var b = new Button
        {
            Width = 40,
            Height = 28,
            Top = 3,
            Text = text,
            BackColor = bg,
            ForeColor = Color.White,
            FlatStyle = FlatStyle.Flat,
            Font = new Font("Segoe UI", 9f, FontStyle.Bold)
        };
        b.FlatAppearance.BorderSize = 0;
        b.Click += onClick;
        return b;
    }

    [DllImport("user32.dll")]
    private static extern bool ReleaseCapture();

    [DllImport("user32.dll")]
    private static extern IntPtr SendMessage(IntPtr hWnd, int msg, int wParam, int lParam);

    [DllImport("uxtheme.dll", CharSet = CharSet.Unicode)]
    private static extern int SetWindowTheme(IntPtr hWnd, string pszSubAppName, string pszSubIdList);
    
    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int dwAttribute, ref int pvAttribute, int cbAttribute);

    private void TryEnableDarkTitleBar()
    {
        try
        {
            if (!IsHandleCreated) return;
            int useDark = 1;
            _ = DwmSetWindowAttribute(Handle, DWMWA_USE_IMMERSIVE_DARK_MODE, ref useDark, sizeof(int));
        }
        catch
        {
            // Best effort only.
        }
    }

    protected override void WndProc(ref Message m)
    {
        if (FormBorderStyle != FormBorderStyle.None)
        {
            base.WndProc(ref m);
            return;
        }
        const int WM_NCHITTEST = 0x84;
        const int WM_NCLBUTTONDBLCLK = 0xA3;
        const int HTCLIENT = 1;
        const int HTCAPTION = 2;
        const int HTLEFT = 10;
        const int HTRIGHT = 11;
        const int HTTOP = 12;
        const int HTTOPLEFT = 13;
        const int HTTOPRIGHT = 14;
        const int HTBOTTOM = 15;
        const int HTBOTTOMLEFT = 16;
        const int HTBOTTOMRIGHT = 17;

        if (m.Msg == WM_NCLBUTTONDBLCLK && (int)m.WParam == HTCAPTION)
        {
            TogglePseudoMaximize();
            return;
        }

        if (m.Msg == WM_NCHITTEST)
        {
            base.WndProc(ref m);
            const int grip = 68;
            var x = (short)((long)m.LParam & 0xFFFF);
            var y = (short)(((long)m.LParam >> 16) & 0xFFFF);
            var p = PointToClient(new Point(x, y));

            if (WindowState == FormWindowState.Normal && !_isPseudoMaximized)
            {
                var left = p.X <= grip;
                var right = p.X >= ClientSize.Width - grip;
                var top = p.Y <= grip;
                var bottom = p.Y >= ClientSize.Height - grip;

                if (left && top) { m.Result = (IntPtr)HTTOPLEFT; return; }
                if (right && top) { m.Result = (IntPtr)HTTOPRIGHT; return; }
                if (left && bottom) { m.Result = (IntPtr)HTBOTTOMLEFT; return; }
                if (right && bottom) { m.Result = (IntPtr)HTBOTTOMRIGHT; return; }
                if (left) { m.Result = (IntPtr)HTLEFT; return; }
                if (right) { m.Result = (IntPtr)HTRIGHT; return; }
                if (top) { m.Result = (IntPtr)HTTOP; return; }
                if (bottom) { m.Result = (IntPtr)HTBOTTOM; return; }
            }

            if ((int)m.Result == HTCLIENT && p.Y <= 34 && p.X < ClientSize.Width - 130)
            {
                m.Result = (IntPtr)HTCAPTION;
                return;
            }
            return;
        }
        base.WndProc(ref m);
    }

    private enum BadgeTone
    {
        Positive,
        Neutral,
        Negative
    }

    private enum TabStateTone
    {
        Off,
        Waiting,
        Up,
        Error
    }

    private sealed class BadgeLabel : Label
    {
        public Color BorderColor { get; set; } = Color.FromArgb(22, 163, 74);

        protected override void OnPaint(PaintEventArgs e)
        {
            using var b = new SolidBrush(BackColor);
            e.Graphics.FillRectangle(b, ClientRectangle);
            using var p = new Pen(BorderColor, 1);
            e.Graphics.DrawRectangle(p, 0, 0, Width - 1, Height - 1);
            TextRenderer.DrawText(e.Graphics, Text, Font, new Rectangle(10, 6, Width - 20, Height - 12), ForeColor, TextFormatFlags.Left | TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis);
        }
    }

    private sealed class InputHostPanel : Panel
    {
        public InputHostPanel()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            var rect = new Rectangle(0, 0, Width - 1, Height - 1);
            using var path = RoundedRect(rect, 4);
            using var fill = new SolidBrush(InputFill);
            using var pen = new Pen(InputBorder, 1);
            e.Graphics.FillPath(fill, path);
            e.Graphics.DrawPath(pen, path);
        }
    }

    private sealed class CardPanel : Panel
    {
        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            var rect = ClientRectangle;
            rect.Width -= 1;
            rect.Height -= 1;
            using var b = new SolidBrush(CardBg);
            e.Graphics.FillRectangle(b, rect);
            using var p = new Pen(CardBorder, 1);
            e.Graphics.DrawRectangle(p, rect);
        }
    }
}
