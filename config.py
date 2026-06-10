"""
Stack #2/#3 paper bot — Configuration centrale.
Pool unique $10k, 2 sleeves non-chevauchants : ORB NQ (jour) + Overnight ES (nuit).
Tous les paramètres ici. Relu par Hippo avant GO launch.
"""

# ═══ Mode ═══
DRY_RUN = True            # addendum #6 : True = log signaux/décisions SANS ordres réels.
                          # Passer à False seulement après validation logique 1-2 jours.

# ═══ Broker IB ═══
IB_HOST = "127.0.0.1"
IB_PORT = 4002            # Gateway paper
IB_CLIENT_ID = 21
IB_ACCOUNT = None         # lu depuis ib_demo.json (hors repo) — jamais en dur dans le code
IB_SECRETS = "~/.openclaw/secrets/ib_demo.json"
IB_RECONNECT_RETRY_SEC = 30
IB_RECONNECT_TIMEOUT_SEC = 300   # 5 min sans reco → halt

# ═══ Pool capital (virtuel, base $10k — JAMAIS le solde IB Demo $1M) ═══
CAPITAL_BASE = 10_000
KILL_EQUITY = 8_000       # equity virtuelle < 8000 (MDD -20%) → arrêt TOTAL des 2 sleeves (global)

# Daily stops PAR SLEEVE (validé Hippo) — PAS de daily stop global :
#   un -4% global bloquerait l'overnight après une mauvaise journée ORB → casse la décorrélation.
ORB_DAILY_LOSS_MAX = 300  # ORB : perte ORB du jour < -$300 (-3%) → plus de nouveau trade ORB ce jour
ON_DAILY_LOSS_MAX = None  # Overnight : PAS de daily stop (1 trade/nuit, le stop 2×ATR suffit)

# ═══ ORB sleeve (NQ_OR30_F1) ═══
ORB_SYMBOL = "MNQ"        # micro Nasdaq
ORB_POINT_VALUE = 2.0     # $/point MNQ
ORB_TICK = 0.25
ORB_OR_MINUTES = 30       # opening range 9:30-10:00 ET
ORB_ENTRY_TF = 5          # détection breakout sur close bougie 5-min
ORB_TARGET_MULT = 1.0     # target = 1× OR width depuis le niveau de cassure
ORB_EMA_PERIOD = 20       # filtre EMA20 daily (convention : close confirmé d-1 vs EMA d-1)
ORB_EOD_EXIT = "15:55"    # exit forcé séance normale (12:55 en demi-séance, géré par calendrier)
ORB_MAX_CONTRACTS = 1

# ═══ Overnight sleeve (V4 EMA+VIX) ═══
ON_SYMBOL = "MES"         # micro S&P
ON_POINT_VALUE = 5.0      # $/point MES
ON_TICK = 0.25
ON_ENTRY_TIME = "15:55"   # entry à la close (12:55 en demi-séance)
ON_EXIT_TIME = "09:29"    # exit juste avant l'open du lendemain (market) ; backtest = open 9:30
ON_EMA_PERIOD = 20        # filtre EMA20 daily (convention : close du jour 15:55 INCLUS vs EMA incl. jour)
ON_VIX_MAX = 25.0         # skip overnight si VIX >= 25 (régime crash)
ON_ATR_PERIOD = 14        # stop = 2× ATR(14) daily
ON_ATR_STOP_MULT = 2.0
ON_MAX_CONTRACTS = 1
# addendum #5 : ON_LEVERAGE_MAX conservé comme GARDE-FOU ANTI-SCALING futur.
# Avec 1 MES fixe il n'est pas binding (levier réel ~5.2× sur notionnel à 1 MES / $5k sleeve,
# mais on size en pool unique $10k donc ~2.6× du pool). Si un jour on scale >1 contrat,
# le risk_manager refusera tout sizing qui pousse le levier notionnel au-delà de ce cap.
ON_LEVERAGE_MAX = 3.0

# ═══ Slippage attendu (pour comparaison live vs backtest, dashboard) ═══
BACKTEST_SLIPPAGE_TICKS = 1.0     # 1 tick/jambe modélisé dans les backtests (appliqué en DRY_RUN)
SLIPPAGE_ALERT_MULT = 2.0         # addendum #7 : flag rouge si slippage réel > 2× backtest

# ═══ Réconciliation P&L virtuel vs IB réel (BUG 4) ═══
RECONCILE_THRESHOLD_USD = 50.0    # écart max P&L virtuel tracké vs realizedPNL IB avant warning

# ═══ Frais commission round-trip (BUG 6) — déduits du P&L NET ═══
# Le slippage (1 tick/jambe) est appliqué dans les fills ; CES frais sont la commission RT.
MNQ_ROUND_TRIP_FEE = 1.50         # $ commission round-trip MNQ
MES_ROUND_TRIP_FEE = 1.12         # $ commission round-trip MES

# ═══ Backtest expected (pour bandes de contrôle dashboard) ═══
# Source : stack2-orb-mvp.md (ORB) + stack3-overnight-drift.md (Overnight V4)
ORB_BACKTEST_OOS_MDD = -4.2       # % (MDD OOS ORB NQ_OR30_F1)
ON_BACKTEST_OOS_MDD = -4.7        # % (MDD OOS Overnight V4)
ORB_BACKTEST_WINRATE = 61.6       # %
ON_BACKTEST_WINRATE = None        # overnight V4 : winrate non central (drift)

# ═══ Calendrier ═══
MARKET_CALENDAR = "NYSE"          # via pandas_market_calendars (source du calendrier)
TIMEZONE = "America/New_York"
HALF_DAY_CLOSE = "13:00"          # demi-séances US (after-Thanksgiving, Xmas eve, July 3...)

# ═══ Heartbeat & logs ═══
HEARTBEAT_SEC = 300               # addendum #8 : log ALIVE toutes les 5 min
LOG_DIR = "logs"
HEARTBEAT_FILE = "logs/heartbeat.log"
DAILY_SUMMARY_FILE = "logs/daily_summary.json"

# ═══ Dashboard ═══
DASH_HOST = "127.0.0.1"           # localhost uniquement
DASH_PORT = 5000

# ═══ Loop ═══
LOOP_SLEEP_SEC = 10               # tick principal

# ═══ Overnight directional mode (addendum #2 — conflit brief résolu) ═══
# Le brief titre "V4 EMA+VIX" mais décrit en step 1a un short si close<EMA (= V5 bidirectionnel).
# Le backtest V4 = LONG-ONLY gated (long si close>EMA ET VIX<25, sinon FLAT — pas de short).
# Défaut = "V4" (réplique exacte du backtest recommandé). "V5" = bidirectionnel (short<EMA, sans gate VIX).
# À confirmer par Hippo à la relecture.
ON_MODE = "V4"
