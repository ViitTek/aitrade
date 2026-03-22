import { useState, useEffect, useRef, useCallback } from "react";
import {
  exportCurrentConfig,
  getConfig,
  getCredentialsStatus,
  listConfigPresets,
  reloadCredentialsEnv,
  runLiveDryRun,
  saveCurrentConfigAsDefaults,
  testCredentials,
  updateConfig,
} from "../../api/bot";
import { usePolling } from "../../hooks/usePolling";

const GROUPS: Record<string, string[]> = {
  Strategy: ["BREAKOUT_N", "EMA_PERIOD", "VOL_FILTER", "VOL_MULT", "COOLDOWN_CANDLES", "ENGINE_BUFFER_MAXLEN", "ENGINE_SEED_CANDLES"],
  Risk: ["RISK_PER_TRADE", "DAILY_STOP", "PROFIT_SPLIT_REINVEST", "ALLOC_PCT", "MIN_USD_ORDER", "PF_GUARD_ENABLED", "PF_GUARD_WINDOW_TRADES", "PF_GUARD_MIN_TRADES", "PF_GUARD_SOFT_THRESHOLD", "PF_GUARD_HARD_THRESHOLD", "PF_GUARD_SOFT_RISK_MULT", "PF_GUARD_HARD_RISK_MULT"],
  Exits: ["SL_ATR_MULT", "TP_ATR_MULT", "TIME_EXIT_MINUTES", "TRAILING_STOP", "TRAIL_ATR_MULT", "TRAIL_ACTIVATION_ATR"],
  Execution: ["FEE_RATE", "SPREAD_BPS", "SLIPPAGE_BPS_BASE", "SLIPPAGE_ATR_MULT", "SLIPPAGE_BPS_CAP", "ATR_PERIOD"],
  Market: ["SYMBOLS", "BINANCE_SYMBOLS", "TRADING_BINANCE_ENABLED", "EXPAND_UNIVERSE_FROM_RECOMMENDATIONS", "INTERVAL_MINUTES", "COLLECT_INTERVALS", "MODE", "MARKET_DATA_POLL_SECONDS"],
  Sentiment: ["SENTIMENT_ENABLED", "SENTIMENT_WINDOW_MINUTES", "SENTIMENT_MIN_ARTICLES", "SENTIMENT_NO_DATA_ACTION"],
  Intel: ["INTEL_ENABLED", "INTEL_POLL_SECONDS", "INTEL_MAX_AGE_MINUTES", "INTEL_BLOCK_LOW_CONF", "LLM_DEGRADED_ACTION", "LLM_DEGRADED_RISK_MULT", "LLM_DEGRADED_MAX_AGE_MINUTES"],
  "Auto Tune": ["AUTO_TUNE_ENABLED", "AUTO_TUNE_APPLY", "AUTO_TUNE_INTERVAL_SECONDS", "AUTO_TUNE_LOOKBACK_DAYS", "AUTO_TUNE_MAX_EVALS", "AUTO_TUNE_MIN_TRADES", "AUTO_TUNE_MIN_WIN_RATE", "AUTO_TUNE_MIN_PROFIT_FACTOR", "AUTO_TUNE_MIN_FINAL_EQUITY"],
  "Dynamic Assets": ["DYNAMIC_ASSETS_ENABLED", "ALWAYS_ACTIVE_SYMBOLS", "MAX_DYNAMIC_SYMBOLS", "MIN_MARKET_CAP_USD", "MIN_VOLUME_24H_USD", "SYMBOL_WARMUP_CANDLES", "RECOMMENDATION_MAX_AGE_MINUTES"],
  "Funding & OI": ["FUNDING_ENABLED", "FUNDING_POLL_SECONDS", "FUNDING_MAX_AGE_MINUTES", "FUNDING_BLOCK_THRESHOLD", "OI_ENABLED", "OI_POLL_SECONDS", "OI_MAX_AGE_MINUTES", "OI_CHANGE_THRESHOLD"],
};

const DESCRIPTIONS: Record<string, string> = {
  // Strategy
  BREAKOUT_N: "Počet svíček pro breakout lookback — hledá N-candle high/low",
  EMA_PERIOD: "Perioda EMA pro trend filtr (signály pouze ve směru trendu)",
  VOL_FILTER: "Volume confirmation — breakout musí být podpořen nadprůměrným objemem",
  VOL_MULT: "Minimální objem svíčky = VOL_MULT × průměrný objem (např. 1.5 = 150%)",
  COOLDOWN_CANDLES: "Pauza po zavření pozice (počet svíček, např. 2 × H1 = 2 hodiny)",
  ENGINE_BUFFER_MAXLEN: "Velikost rolling bufferu svíček na symbol (větší = delší kontext pro rozhodování)",
  ENGINE_SEED_CANDLES: "Kolik historických svíček načíst z Mongo při startu engine",
  // Risk
  RISK_PER_TRADE: "Risk na obchod jako podíl equity (0.005 = 0.5%)",
  DAILY_STOP: "Denní stop-loss limit / kill switch (0.02 = 2% denní ztráty zastaví trading)",
  PROFIT_SPLIT_REINVEST: "Podíl zisku reinvestovaný zpět (0.5 = 50% reinvest, 50% do bufferu)",
  ALLOC_PCT: "Procento equity alokované na jednu pozici v paper mode (0.10 = 10%)",
  MIN_USD_ORDER: "Minimální velikost objednávky v USDT",
  PF_GUARD_ENABLED: "Zapnout rolling PF guard (auto throttle / block při zhoršení výkonu)",
  PF_GUARD_WINDOW_TRADES: "Počet posledních obchodů pro výpočet rolling Profit Factor",
  PF_GUARD_MIN_TRADES: "Minimum obchodů v okně, než se PF guard začne aplikovat",
  PF_GUARD_SOFT_THRESHOLD: "PF pod tímto prahem přepne na snížený risk",
  PF_GUARD_HARD_THRESHOLD: "PF pod tímto prahem blokuje nové vstupy",
  PF_GUARD_SOFT_RISK_MULT: "Risk multiplier při soft guard režimu (např. 0.5)",
  PF_GUARD_HARD_RISK_MULT: "Risk multiplier při hard guard režimu (0 = blokace)",
  // Exits
  SL_ATR_MULT: "Stop loss vzdálenost = SL_ATR_MULT × ATR (1.5 = 1.5× ATR od vstupu)",
  TP_ATR_MULT: "Take profit vzdálenost = TP_ATR_MULT × ATR (4.0 → R:R poměr 1:2.67)",
  TIME_EXIT_MINUTES: "Časový exit v minutách — zavře pozici po N minutách (720 = 12h)",
  TRAILING_STOP: "Zapnout/vypnout trailing stop (posouvá SL za cenou)",
  TRAIL_ATR_MULT: "Trailing distance = TRAIL_ATR_MULT × ATR (vzdálenost trail stopu od ceny)",
  TRAIL_ACTIVATION_ATR: "Trailing stop se aktivuje po pohybu ≥ N × ATR ve směru obchodu",
  // Execution
  FEE_RATE: "Poplatek za stranu (0.0008 = 0.08%, aplikuje se na entry i exit)",
  SPREAD_BPS: "Spread model v basis points (2.0 bps = 0.02%, split na obě strany)",
  SLIPPAGE_BPS_BASE: "Minimální slippage v basis points",
  SLIPPAGE_ATR_MULT: "Dynamický přídavek slippage = (ATR/price) × tento multiplikátor",
  SLIPPAGE_BPS_CAP: "Maximální slippage v basis points (horní limit)",
  ATR_PERIOD: "Perioda ATR pro výpočet dynamické slippage a position sizingu",
  // Market
  SYMBOLS: "Kraken trading páry oddělené čárkou (např. BTC/USDT,ETH/USDT)",
  BINANCE_SYMBOLS: "Binance symboly pro sběr dat oddělené čárkou (např. PAXG/USDT,SOL/USDT)",
  TRADING_BINANCE_ENABLED: "Zapnout Binance feed i pro trading engine (rozšíří rozhodovací universe)",
  EXPAND_UNIVERSE_FROM_RECOMMENDATIONS: "Přidá do universe i latest LLM doporučené symboly",
  INTERVAL_MINUTES: "Interval svíček v minutách (60 = H1, 5 = M5, 15 = M15)",
  COLLECT_INTERVALS: "Intervaly pro collector oddělené čárkou (např. 5,15,60 = M5/M15/H1 paralelně)",
  MODE: "Režim tradingu: paper (simulace) nebo live (reálné obchody)",
  MARKET_DATA_POLL_SECONDS: "Interval pollingu market dat v sekundách (300 = 5 min)",
  // Sentiment
  SENTIMENT_ENABLED: "Zapnout/vypnout sentiment filtr (blokuje signály proti sentimentu)",
  SENTIMENT_WINDOW_MINUTES: "Okno pro hledání sentimentu — jak daleko zpět v minutách",
  SENTIMENT_MIN_ARTICLES: "Minimum článků potřebných pro rozhodnutí o sentimentu",
  SENTIMENT_NO_DATA_ACTION: "Co dělat když nejsou data: \"pass\" = signál projde, \"block\" = zablokuje",
  // Intel
  INTEL_ENABLED: "Zapnout/vypnout market intelligence filtr (LLM analýza trhu)",
  INTEL_POLL_SECONDS: "Interval pollingu market intel v sekundách (900 = 15 min)",
  INTEL_MAX_AGE_MINUTES: "Ignorovat intel starší než N minut (120 = 2 hodiny)",
  INTEL_BLOCK_LOW_CONF: "Blokovat obchody kde LLM confidence je LOW",
  LLM_DEGRADED_ACTION: "Chování při LLM fail stavu: pass, throttle nebo block",
  LLM_DEGRADED_RISK_MULT: "Risk multiplier při LLM degraded = throttle",
  LLM_DEGRADED_MAX_AGE_MINUTES: "Jak dlouho po LLM fail považovat stav za degraded",
  // Auto Tune
  AUTO_TUNE_ENABLED: "Zapnout periodický optimizer configu nad Mongo backtest daty",
  AUTO_TUNE_APPLY: "Automaticky aplikovat vybraný návrh do běžícího runtime settings",
  AUTO_TUNE_INTERVAL_SECONDS: "Jak často spouštět auto-tune worker (sekundy)",
  AUTO_TUNE_LOOKBACK_DAYS: "Kolik dní zpět použít pro optimalizační backtest okno",
  AUTO_TUNE_MAX_EVALS: "Max počet kandidátů testovaných v jednom auto-tune běhu",
  AUTO_TUNE_MIN_TRADES: "Min obchodů nutných pro validní kandidát",
  AUTO_TUNE_MIN_WIN_RATE: "Min win rate pro povolení auto-apply",
  AUTO_TUNE_MIN_PROFIT_FACTOR: "Min profit factor pro povolení auto-apply",
  AUTO_TUNE_MIN_FINAL_EQUITY: "Min final equity pro povolení auto-apply",
  // Dynamic Assets
  DYNAMIC_ASSETS_ENABLED: "Master switch — LLM dynamicky vybírá na čem obchodovat",
  ALWAYS_ACTIVE_SYMBOLS: "Symboly které jsou vždy aktivní (nikdy neodstraněné LLM)",
  MAX_DYNAMIC_SYMBOLS: "Maximální počet LLM-doporučených symbolů (mimo always-active)",
  MIN_MARKET_CAP_USD: "Minimální market cap v USD pro doporučení (1B = 1000000000)",
  MIN_VOLUME_24H_USD: "Minimální 24h objem v USD pro doporučení (50M = 50000000)",
  SYMBOL_WARMUP_CANDLES: "Počet svíček potřebných před zahájením tradingu na novém symbolu",
  RECOMMENDATION_MAX_AGE_MINUTES: "Maximální stáří doporučení v minutách (starší se ignorují)",
  // Funding & OI
  FUNDING_ENABLED: "Zapnout/vypnout funding rate filtr (blokuje signály při extrémním FR)",
  FUNDING_POLL_SECONDS: "Interval pollingu funding rate v sekundách (300 = 5 min)",
  FUNDING_MAX_AGE_MINUTES: "Ignorovat funding data starší než N minut",
  FUNDING_BLOCK_THRESHOLD: "Absolutní FR threshold pro blokování (0.01 = 1%)",
  OI_ENABLED: "Zapnout/vypnout open interest filtr (detekuje false breakouty)",
  OI_POLL_SECONDS: "Interval pollingu open interest v sekundách (300 = 5 min)",
  OI_MAX_AGE_MINUTES: "Ignorovat OI data starší než N minut",
  OI_CHANGE_THRESHOLD: "Pokles OI v % pro blokování signálu (0.10 = 10%)",
};

export default function ConfigEditor() {
  const [config, setConfig] = useState<Record<string, any> | null>(null);
  const [dirty, setDirty] = useState<Record<string, any>>({});
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [presetFiles, setPresetFiles] = useState<string[]>([]);
  const [selectedPresetFile, setSelectedPresetFile] = useState<string>("");
  const [credStatus, setCredStatus] = useState<{
    kraken: { configured: boolean };
    binance: { configured: boolean };
    mode: string;
    source?: string;
    env_path?: string;
  } | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const { data: cfgData } = usePolling(useCallback(() => getConfig(), []), 120000, "config");
  const { data: presetData } = usePolling(useCallback(() => listConfigPresets(), []), 120000, "config_presets");
  const { data: credsData } = usePolling(useCallback(() => getCredentialsStatus(), []), 60000, "cred_status");

  useEffect(() => {
    if (!config && cfgData) setConfig(cfgData as Record<string, any>);
  }, [cfgData, config]);

  useEffect(() => {
    if (!presetData) return;
    const files = presetData.files || [];
    setPresetFiles(files);
    if ((!selectedPresetFile || !files.includes(selectedPresetFile)) && files.length > 0) {
      setSelectedPresetFile(files[0]);
    }
  }, [presetData, selectedPresetFile]);

  useEffect(() => {
    if (credsData) setCredStatus(credsData);
  }, [credsData]);

  if (!config) return <div className="text-gray-500 text-sm p-4">Loading config...</div>;

  const handleChange = (key: string, value: any) => {
    setDirty((d) => ({ ...d, [key]: value }));
    setMsg(null);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const updates: Record<string, any> = {};
      for (const [k, v] of Object.entries(dirty)) {
        const orig = config[k];
        if (typeof orig === "number") updates[k] = Number(v);
        else if (typeof orig === "boolean") updates[k] = v === "true" || v === true;
        else updates[k] = v;
      }
      const res = await updateConfig(updates);
      setConfig((c) => (c ? { ...c, ...res.updated } : c));
      setDirty({});
      setMsg(`Updated ${Object.keys(res.updated).length} params`);
    } catch (e: any) {
      setMsg(`Error: ${e.message}`);
    }
    setSaving(false);
  };

  const applyJsonConfig = (raw: Record<string, any>) => {
    if (!config) return;
    const allowedKeys = new Set(Object.keys(config));
    const imported: Record<string, any> = {};
    for (const [k, v] of Object.entries(raw || {})) {
      const key = String(k).toUpperCase();
      if (allowedKeys.has(key)) {
        imported[key] = v;
      }
    }
    setDirty((d) => ({ ...d, ...imported }));
    setMsg(`Loaded ${Object.keys(imported).length} params from JSON`);
  };

  const handleLoadFile = async (file: File) => {
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("JSON must be an object");
      }
      applyJsonConfig(parsed as Record<string, any>);
    } catch (e: any) {
      setMsg(`Error loading JSON: ${e.message}`);
    }
  };

  const loadPreset = async (presetPath: string) => {
    try {
      const r = await fetch(presetPath);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const parsed = await r.json();
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("Preset JSON must be an object");
      }
      applyJsonConfig(parsed as Record<string, any>);
    } catch (e: any) {
      setMsg(`Error loading preset: ${e.message}`);
    }
  };

  const loadProjectPreset = async () => {
    if (!selectedPresetFile) return;
    await loadPreset(`/config-presets/${selectedPresetFile}`);
  };

  const handleExportJson = () => {
    setSaving(true);
    setMsg(null);
    (async () => {
      try {
        if (Object.keys(dirty).length > 0) {
          const updates: Record<string, any> = {};
          for (const [k, v] of Object.entries(dirty)) {
            const orig = config?.[k];
            if (typeof orig === "number") updates[k] = Number(v);
            else if (typeof orig === "boolean") updates[k] = v === "true" || v === true;
            else updates[k] = v;
          }
          const res = await updateConfig(updates);
          setConfig((c) => (c ? { ...c, ...res.updated } : c));
          setDirty({});
        }
        const exported = await exportCurrentConfig();
        setMsg(`Exported to ${exported.filename}`);
      } catch (e: any) {
        setMsg(`Error exporting JSON: ${e.message}`);
      }
      setSaving(false);
    })();
  };

  const handleSaveAsDefault = async () => {
    setSaving(true);
    setMsg(null);
    try {
      if (Object.keys(dirty).length > 0) {
        const updates: Record<string, any> = {};
        for (const [k, v] of Object.entries(dirty)) {
          const orig = config[k];
          if (typeof orig === "number") updates[k] = Number(v);
          else if (typeof orig === "boolean") updates[k] = v === "true" || v === true;
          else updates[k] = v;
        }
        const res = await updateConfig(updates);
        setConfig((c) => (c ? { ...c, ...res.updated } : c));
        setDirty({});
      }
      const saved = await saveCurrentConfigAsDefaults();
      setMsg(`Saved startup defaults (${saved.saved} params)`);
    } catch (e: any) {
      setMsg(`Error saving defaults: ${e.message}`);
    }
    setSaving(false);
  };

  const renderField = (key: string) => {
    const val = key in dirty ? dirty[key] : config[key];
    const orig = config[key];

    if (typeof orig === "boolean") {
      return (
        <select
          value={String(val)}
          onChange={(e) => handleChange(key, e.target.value === "true")}
          className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm w-32"
        >
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      );
    }

    return (
      <input
        type={typeof orig === "number" ? "number" : "text"}
        value={val ?? ""}
        step={typeof orig === "number" ? "any" : undefined}
        onChange={(e) => handleChange(key, e.target.value)}
        className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm w-48"
      />
    );
  };

  const refreshCreds = async () => {
    const c = await getCredentialsStatus();
    setCredStatus(c);
  };

  const handleReloadCreds = async () => {
    try {
      const r = await reloadCredentialsEnv();
      await refreshCreds();
      setMsg(`Credentials reloaded from ${r.env_path}`);
    } catch (e: any) {
      setMsg(`Credentials reload failed: ${e.message}`);
    }
  };

  const handleTestCreds = async (exchange: "kraken" | "binance") => {
    try {
      const r = await testCredentials(exchange);
      await refreshCreds();
      setMsg(`${exchange.toUpperCase()} credentials test: ${r.ok ? "OK" : "FAILED"} (${r.message})`);
    } catch (e: any) {
      setMsg(`${exchange.toUpperCase()} test failed: ${e.message}`);
    }
  };

  const handleLiveDryRun = async () => {
    try {
      const r = await runLiveDryRun();
      setMsg(
        `Live dry-run OK | Kraken: ${r.kraken.message} | Binance: ${r.binance.message} | Orders placed: ${r.orders_placed}`
      );
    } catch (e: any) {
      setMsg(`Live dry-run failed: ${e.message}`);
    }
  };

  return (
    <div className="space-y-6">
      <div className="bg-gray-900 rounded-lg border border-gray-800 p-4">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide mb-3">Credentials (Env-only)</h3>
        <p className="text-xs text-gray-500 mb-3">
          API keys are not editable in UI. Put keys into <code>python-core/.env</code>, then reload and test here.
        </p>
        {msg && (
          <div className="mb-3 px-3 py-2 rounded border border-gray-700 bg-gray-800 text-sm text-green-400">
            {msg}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-3 mb-3">
          <span className={`px-2 py-1 rounded text-xs ${credStatus?.kraken?.configured ? "bg-emerald-900 text-emerald-300" : "bg-red-900 text-red-300"}`}>
            Kraken: {credStatus?.kraken?.configured ? "configured" : "missing"}
          </span>
          <span className={`px-2 py-1 rounded text-xs ${credStatus?.binance?.configured ? "bg-emerald-900 text-emerald-300" : "bg-red-900 text-red-300"}`}>
            Binance: {credStatus?.binance?.configured ? "configured" : "missing"}
          </span>
          <span className="px-2 py-1 rounded text-xs bg-slate-800 text-slate-300">
            Mode: {credStatus?.mode ?? "unknown"}
          </span>
          {credStatus?.env_path && (
            <span className="px-2 py-1 rounded text-xs bg-slate-800 text-slate-400">
              {credStatus.env_path}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={handleReloadCreds}
            className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded text-sm font-medium transition-colors"
          >
            Reload .env
          </button>
          <button
            onClick={() => handleTestCreds("kraken")}
            className="px-4 py-2 bg-indigo-700 hover:bg-indigo-600 rounded text-sm font-medium transition-colors"
          >
            Test Kraken
          </button>
          <button
            onClick={() => handleTestCreds("binance")}
            className="px-4 py-2 bg-indigo-700 hover:bg-indigo-600 rounded text-sm font-medium transition-colors"
          >
            Test Binance
          </button>
          <button
            onClick={handleLiveDryRun}
            className="px-4 py-2 bg-emerald-700 hover:bg-emerald-600 rounded text-sm font-medium transition-colors"
          >
            Live Dry-Run
          </button>
        </div>
      </div>

      <div className="flex items-center gap-4">
        <input
          ref={fileInputRef}
          type="file"
          accept=".json,application/json"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) {
              handleLoadFile(f);
            }
            e.currentTarget.value = "";
          }}
        />
        <button
          onClick={() => fileInputRef.current?.click()}
          className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm font-medium transition-colors"
        >
          Load JSON File
        </button>
        <select
          value={selectedPresetFile}
          onChange={(e) => setSelectedPresetFile(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm min-w-64"
        >
          {presetFiles.length === 0 ? (
            <option value="">No project JSON presets</option>
          ) : (
            presetFiles.map((f) => (
              <option key={f} value={f}>{f}</option>
            ))
          )}
        </select>
        <button
          onClick={loadProjectPreset}
          disabled={presetFiles.length === 0 || !selectedPresetFile}
          className="px-4 py-2 bg-indigo-700 hover:bg-indigo-600 rounded text-sm font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          Load Project JSON
        </button>
        <button
          onClick={handleExportJson}
          disabled={saving}
          className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded text-sm font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          Export JSON
        </button>
        <button
          onClick={handleSaveAsDefault}
          disabled={saving}
          className="px-4 py-2 bg-violet-700 hover:bg-violet-600 rounded text-sm font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          Save as Startup Default
        </button>
        <button
          onClick={() => loadPreset("/config-presets/safe-profile.json")}
          className="px-4 py-2 bg-emerald-700 hover:bg-emerald-600 rounded text-sm font-medium transition-colors"
        >
          Load Safe Preset
        </button>
        <button
          onClick={() => loadPreset("/config-presets/aggressive-profile.json")}
          className="px-4 py-2 bg-orange-700 hover:bg-orange-600 rounded text-sm font-medium transition-colors"
        >
          Load Aggressive Preset
        </button>
        <button
          onClick={() => loadPreset("/config-presets/optimized-profile.json")}
          className="px-4 py-2 bg-cyan-700 hover:bg-cyan-600 rounded text-sm font-medium transition-colors"
        >
          Load Optimized Preset
        </button>
        <button
          onClick={() => loadPreset("/config-presets/safe-downtrend-profile.json")}
          className="px-4 py-2 bg-amber-700 hover:bg-amber-600 rounded text-sm font-medium transition-colors"
        >
          Load Safe-Downtrend Preset
        </button>
        <button
          onClick={handleSave}
          disabled={saving || Object.keys(dirty).length === 0}
          className="px-6 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {saving ? "Saving..." : "Save Changes"}
        </button>
        {Object.keys(dirty).length > 0 && (
          <span className="text-xs text-yellow-400">{Object.keys(dirty).length} unsaved changes</span>
        )}
      </div>

      {Object.entries(GROUPS).map(([group, keys]) => (
        <div key={group} className="bg-gray-900 rounded-lg border border-gray-800 p-4">
          <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide mb-3">{group}</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {keys.filter((k) => k in config).map((k) => (
              <div key={k} className="space-y-1">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs text-gray-400 font-mono">{k}</span>
                  {renderField(k)}
                </div>
                {DESCRIPTIONS[k] && (
                  <p className="text-[11px] text-gray-500 leading-tight">{DESCRIPTIONS[k]}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}

    </div>
  );
}
