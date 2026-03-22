# trading/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from project_layout import get_layout


_LAYOUT = get_layout()
_PYTHON_CORE_DIR = _LAYOUT["project_dir"] / "python-core"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_PYTHON_CORE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )
    # --- DB ---
    MONGO_URI: str = "mongodb://127.0.0.1:27017"
    MONGO_DB: str = "aiinvest"

    # --- Market feed (Kraken) ---
    KRAKEN_WS_URL: str = "wss://ws.kraken.com/v2"
    SYMBOLS: str = "BTC/USDT,ETH/USDT"
    INTERVAL_MINUTES: int = 60                # H1 svĂ­ÄŤky (vĂ˝chozĂ­ pro trading i collector)
    COLLECT_INTERVALS: str = "1,5,15,60"      # timeframey pro collector (M1/M5/M15/H1)

    # --- Market feed (Binance) ---
    BINANCE_WS_URL: str = "wss://stream.binance.com:9443"
    BINANCE_SYMBOLS: str = "PAXG/USDT,SOL/USDT,BNB/USDT,XRP/USDT,DOGE/USDT,TRX/USDT,USDC/USDT"  # pĂˇry sbĂ­ranĂ© z Binance
    TRADING_BINANCE_ENABLED: bool = True         # povolĂ­ Binance feed i pro live trading engine
    TRADING_IBKR_ENABLED: bool = False
    IBKR_SYMBOLS: str = "EURUSD,GBPUSD,USDJPY,XAUUSD,XAGUSD,CL"
    EXPAND_UNIVERSE_FROM_RECOMMENDATIONS: bool = False  # stable runtime default: nerozšiřuj universe bez explicitního rozhodnutí

    # --- Mode ---
    MODE: str = "paper"   # paper | live (live aĹľ pozdÄ›ji)

    # --- Risk / portfolio rules ---
    RISK_PER_TRADE: float = 0.003         # optimized (data-driven run): 0.3% risk per trade
    DAILY_STOP: float = 0.02              # 2% start (kill switch)
    PROFIT_SPLIT_REINVEST: float = 0.6    # safe default: 60% reinvest, 40% buffer
    PF_GUARD_ENABLED: bool = True         # rolling PF guard (auto throttle / block entries)
    PF_GUARD_WINDOW_TRADES: int = 30      # rolling window uzavĹ™enĂ˝ch obchodĹŻ
    PF_GUARD_MIN_TRADES: int = 12         # minimum obchodĹŻ pro aktivaci guardu
    PF_GUARD_SOFT_THRESHOLD: float = 1.05 # PF pod threshold -> snĂ­ĹľenĂ˝ risk
    PF_GUARD_HARD_THRESHOLD: float = 0.90 # PF pod threshold -> blokace novĂ˝ch vstupĹŻ
    PF_GUARD_SOFT_RISK_MULT: float = 0.5  # risk multiplier v soft reĹľimu
    PF_GUARD_HARD_RISK_MULT: float = 0.0  # risk multiplier v hard reĹľimu (0 = block)
    PF_GUARD_NON_CRYPTO_ENABLED: bool = False  # apply PF guard also to non-crypto (IBKR) symbols

    # --- Strategy params ---
    BREAKOUT_N: int = 7                   # safe default breakout lookback
    EMA_PERIOD: int = 50                  # EMA perioda pro trend filtr
    VOL_FILTER: bool = True               # volume confirmation pro breakout
    VOL_MULT: float = 1.3                 # optimized volume threshold
    COOLDOWN_CANDLES: int = 1             # optimized cooldown
    ENGINE_BUFFER_MAXLEN: int = 1000      # velikost rolling bufferu svĂ­ÄŤek na symbol
    ENGINE_SEED_CANDLES: int = 800        # kolik svĂ­ÄŤek naÄŤĂ­st z Mongo pĹ™i startu

    # --- Execution realism (Realistic+) ---
    # Fees: nastav dle fee tieru. Pro paper nech rozumnĂ˝ default.
    # Single global fee model (engine does not distinguish exchange-specific fees).
    # Use realistic baseline aligned to Binance spot account fee level.
    FEE_RATE: float = 0.0010              # 0.10% per side (entry i exit)
    FEE_RATE_BINANCE: float = 0.0010      # 0.10% per side (spot account baseline)
    FEE_RATE_KRAKEN: float = 0.0025       # 0.25% per side (Kraken maker baseline)
    DEFAULT_BROKER: str = "kraken"        # kraken | binance | ibkr
    FEE_RATE_IBKR_FX: float = 0.00002     # 0.0020% per side (placeholder tier baseline)
    FEE_RATE_IBKR_FUTURES: float = 0.00008 # 0.0080% per side (placeholder tier baseline)
    FEE_RATE_IBKR_STOCKS: float = 0.00005 # 0.0050% per side (placeholder tier baseline)

    # Spread model:
    SPREAD_BPS: float = 2.0               # 2 bps celkovĂ˝ spread (engine/exec pouĹľije spread/2 na kaĹľdou stranu)

    # Slippage model (bps):
    SLIPPAGE_BPS_BASE: float = 1.5        # minimĂˇlnĂ­ slippage
    SLIPPAGE_ATR_MULT: float = 50.0       # pĹ™Ă­davek = ATR/price * ATR_MULT (v bps)
    SLIPPAGE_BPS_CAP: float = 25.0        # max slippage

    ATR_PERIOD: int = 14                  # ATR perioda pro dynamickou slippage

    # Position sizing (paper):
    # JednoduchĂ˝ sizing podle equity (pro teÄŹ). AĹľ pĹ™idĂˇme stop-loss, pĹ™epneme to na RISK_PER_TRADE.
    ALLOC_PCT: float = 0.10               # 10% equity do pozice (paper)
    MIN_USD_ORDER: float = 10.0           # minimĂˇlnĂ­ velikost pozice v USDT

    # Exits:
    SL_ATR_MULT: float = 1.2             # optimized stop loss multiplier
    TP_ATR_MULT: float = 3.0             # optimized take profit multiplier
    TIME_EXIT_MINUTES: float = 1440.0    # time exit v minutách (24h)

    # Trailing stop:
    TRAILING_STOP: bool = True            # zapnout trailing stop
    TRAIL_ATR_MULT: float = 1.0           # trailing distance = TRAIL_ATR_MULT Ă— ATR (tight po aktivaci)
    TRAIL_ACTIVATION_ATR: float = 2.0     # aktivace trailing stopu po pohybu â‰Ą 2Ă— ATR ve smÄ›ru obchodu
    FEE_AWARE_GATE_ENABLED: bool = True   # block signal when expected edge does not cover costs
    FEE_AWARE_MIN_EDGE_MULT: float = 1.2  # required expected edge / estimated cost ratio

    # Equity snapshots:
    EQUITY_MARK_EVENT: str = "mark"       # event tag pro mark-to-market snapshoty

    # --- Sentiment filter ---
    SENTIMENT_ENABLED: bool = False
    SENTIMENT_WINDOW_MINUTES: int = 60       # jak daleko zpÄ›t hledat sentiment
    SENTIMENT_MIN_ARTICLES: int = 1          # minimum ÄŤlĂˇnkĹŻ potĹ™ebnĂ˝ch pro rozhodnutĂ­
    SENTIMENT_NO_DATA_ACTION: str = "pass"   # "pass" = signĂˇl projde, "block" = zablokuje

    # --- Market Intelligence ---
    INTEL_ENABLED: bool = False              # off by default, zapni aĹľ bÄ›ĹľĂ­ market_intel_worker
    INTEL_POLL_SECONDS: int = 900            # polling interval pro market_intel_worker (15 min)
    INTEL_MAX_AGE_MINUTES: int = 120         # ignoruj intel starĹˇĂ­ neĹľ 2h
    INTEL_BLOCK_LOW_CONF: bool = False       # blokuj trades kde confidence je LOW
    LLM_DEGRADED_ACTION: str = "throttle"    # "pass" | "throttle" | "block"
    LLM_DEGRADED_RISK_MULT: float = 0.35     # risk multiplier pĹ™i degraded LLM reĹľimu
    LLM_DEGRADED_MAX_AGE_MINUTES: int = 180  # degradaci Ĺ™eĹˇ jen pro ÄŤerstvĂ© LLM fail zĂˇznamy
    LLM_POLICY_LOG_DECISIONS: bool = True     # logovat LLM policy pass/throttle/block rozhodnutĂ­
    LLM_NON_BLOCKING_MODE: bool = False       # pokud ON, LLM je advisory-only (neblokuje vstupy)

    # --- Dynamic Asset Selection ---
    DYNAMIC_ASSETS_ENABLED: bool = False           # stable runtime default: bez dynamického přidávání aktiv
    ALWAYS_ACTIVE_SYMBOLS: str = "BTC/USDT,ETH/USDT"  # symboly kterĂ© nikdy neodstranĂ­
    MAX_DYNAMIC_SYMBOLS: int = 6                   # max LLM-doporuÄŤenĂ˝ch symbolĹŻ (mimo always-active)
    MIN_MARKET_CAP_USD: float = 1_000_000_000      # 1B minimum market cap
    MIN_VOLUME_24H_USD: float = 50_000_000         # 50M minimum dennĂ­ objem
    SYMBOL_WARMUP_CANDLES: int = 50                # svĂ­ÄŤek potĹ™eba pĹ™ed tradingem
    RECOMMENDATION_MAX_AGE_MINUTES: int = 180      # max stĂˇĹ™Ă­ doporuÄŤenĂ­ (3h)

    # --- Funding Rate & Open Interest ---
    FUNDING_ENABLED: bool = True                  # master switch pro funding rate filtr
    FUNDING_POLL_SECONDS: int = 300               # polling interval (5 min)
    FUNDING_MAX_AGE_MINUTES: int = 60             # max stĂˇĹ™Ă­ dat pro filtr
    FUNDING_BLOCK_THRESHOLD: float = 0.01         # |FR| > 1% â†’ blokuj contrarian signĂˇly
    OI_ENABLED: bool = False                      # master switch pro open interest filtr
    OI_POLL_SECONDS: int = 300                    # polling interval (5 min)
    OI_MAX_AGE_MINUTES: int = 60                  # max stĂˇĹ™Ă­ dat pro filtr
    OI_CHANGE_THRESHOLD: float = 0.10             # 10% pokles OI â†’ false breakout risk

    # --- Market Data Dashboard ---
    MARKET_DATA_POLL_SECONDS: int = 300           # polling interval pro market_data_worker (5 min)
    NEWS_WORKER_ENABLED: bool = True              # allow disabling shared news worker in secondary stacks
    MARKET_DATA_WORKER_ENABLED: bool = True       # allow disabling shared market data worker in secondary stacks

    # --- Cross-Asset Shadow (data-only, no execution) ---
    CROSS_ASSET_SHADOW_ENABLED: bool = False      # zapnout sbÄ›r ne-krypto trhĹŻ do shadow reĹľimu
    CROSS_ASSET_POLL_SECONDS: int = 300           # polling interval (5 min)
    CROSS_ASSET_PROVIDER: str = "stooq"           # stooq | ibkr | oanda (ibkr/oanda vyĹľadujĂ­ ĂşÄŤet)
    CROSS_ASSET_FX_SYMBOLS: str = "EURUSD,GBPUSD,USDJPY,AUDUSD"
    CROSS_ASSET_COMMODITY_SYMBOLS: str = "XAUUSD,XAGUSD,WTI,BRENT"
    CROSS_ASSET_INDEX_SYMBOLS: str = "SPX,NDX,DAX,FTSE"

    # --- Auto Config Optimizer ---
    AUTO_TUNE_ENABLED: bool = False               # periodicky vyhodnocuj a navrhuj config zmÄ›ny
    AUTO_TUNE_APPLY: bool = False                 # automaticky aplikovat nĂˇvrhy do runtime settings
    AUTO_TUNE_INTERVAL_SECONDS: int = 21600       # 6 hodin
    AUTO_TUNE_LOOKBACK_DAYS: int = 60             # backtest okno pro optimalizaci
    AUTO_TUNE_MAX_EVALS: int = 24                 # max poÄŤet kandidĂˇtĹŻ v jednĂ© optimalizaci
    AUTO_TUNE_MIN_TRADES: int = 20                # minimĂˇlnĂ­ poÄŤet obchodĹŻ pro validnĂ­ kandidĂˇt
    AUTO_TUNE_MIN_WIN_RATE: float = 0.50          # minimĂˇlnĂ­ win rate pro apply
    AUTO_TUNE_MIN_PROFIT_FACTOR: float = 1.0      # minimĂˇlnĂ­ profit factor pro apply
    AUTO_TUNE_MIN_FINAL_EQUITY: float = 1000.0    # minimĂˇlnĂ­ final equity pro apply

    # --- Signal Quality Scorer (tabular policy overlay) ---
    SIGNAL_QUALITY_ENABLED: bool = False           # zapnout ML quality gate (neĹ™Ă­dĂ­ BUY/SELL smÄ›r)
    SIGNAL_QUALITY_MIN_PROB: float = 0.55          # minimĂˇlnĂ­ pravdÄ›podobnost "kvalitnĂ­ho" signĂˇlu
    SIGNAL_QUALITY_THROTTLE_PROB: float = 0.62     # pod touto hranicĂ­ snĂ­Ĺľit risk (pokud nenĂ­ zablokovĂˇno)
    SIGNAL_QUALITY_LOW_RISK_MULT: float = 0.60     # risk multiplier v throttle zĂłnÄ›
    SIGNAL_QUALITY_LOG_DECISIONS: bool = True      # logovat i pass/throttle rozhodnutĂ­ quality policy
    SIGNAL_QUALITY_HORIZON_MIN: int = 240          # cĂ­lovĂ˝ horizont labelu (ret_{horizon}m > 0)
    SIGNAL_QUALITY_SHADOW_HORIZON_MIN: int = 60    # shadow evaluace na 60 min jako hlavní runtime baseline
    SIGNAL_QUALITY_LOOKBACK_DAYS: int = 360        # kolik dnĂ­ historie pouĹľĂ­t pro trĂ©nink modelu
    SIGNAL_QUALITY_MIN_SAMPLES: int = 200          # minimĂˇlnĂ­ poÄŤet validnĂ­ch trĂ©ninkovĂ˝ch vzorkĹŻ

    # --- Shadow mode (live dry-run) ---
    SHADOW_MODE_ENABLED: bool = False              # v MODE=live neprovĂˇdÄ›t exekuci, jen logovat "would execute"

    # --- Runtime resume ---
    RESUME_ON_START: bool = True                  # pĹ™i startu API/bota navĂˇzat na poslednĂ­ paper run

    # --- Live (zatĂ­m prĂˇzdnĂ©) ---
    KRAKEN_API_KEY: str = ""
    KRAKEN_API_SECRET: str = ""
    BINANCE_API_KEY: str = ""
    BINANCE_API_SECRET: str = ""
    IBKR_TWS_HOST: str = "127.0.0.1"
    IBKR_TWS_PORT: int = 7497
    IBKR_GATEWAY_TRADING_MODE: str = "paper"
    IBKR_CLIENT_ID: int = 77
    IBKR_READONLY_API: bool = True


settings = Settings()

