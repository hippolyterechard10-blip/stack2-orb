# Stack #2/#3 — Bot paper trading ORB NQ + Overnight ES

Double-stratégie futures micro sur Interactive Brokers paper, pool unique.
**État : CODÉ, NON LANCÉ. `DRY_RUN=True`. En audit.**

## Les 2 sleeves (non-chevauchants : jour / nuit)
- **ORB NQ_OR30_F1** (`strategy_orb.py`) — opening range 30 min (9:30-10:00 ET), filtre EMA20 daily, breakout 5-min, stop = OR opposé, target = 1× OR width, EOD exit 15:55, max 1 trade/jour. 1 MNQ.
- **Overnight ES V4** (`strategy_overnight.py`) — long à la close 15:55, exit à l'open 9:30, filtre EMA20 + VIX<25, **LONG-ONLY gated** (jamais de short), stop 2×ATR. 1 MES. Held vendredi→lundi.

## Carte des fichiers
| Fichier | Rôle |
|---|---|
| `config.py` | Tous les paramètres (DRY_RUN, sizing, kill switches, ON_MODE V4/V5) |
| `calendar_util.py` | Calendrier NYSE (fériés, demi-séances) via pandas_market_calendars |
| `broker_ib.py` | Couche IB ib_insync (DRY_RUN-aware, reconnexion, front-month MNQ/MES) |
| `risk_manager.py` | Pool $10k, kill switch −20% global, daily stops PAR sleeve, anti-double-position |
| `strategy_orb.py` | Sleeve ORB |
| `strategy_overnight.py` | Sleeve Overnight V4 |
| `logger.py` | Logs JSONL/jour + daily_summary + heartbeat |
| `dashboard.py` | Flask localhost:5000 (status, trades, chart, config) |
| `main.py` | Event loop, séquencement ORB→Overnight, heartbeat |
| `run.sh` + `*.plist.disabled` | launchd auto-restart (NON chargé) |
| `NOTES_PHASE_D.md` | **À LIRE EN PREMIER** — décisions d'implémentation détaillées |

## Priorités d'audit (par criticité)
1. **`risk_manager.py`** — dernière ligne de défense (kill switches, anti-double-marge).
2. **Conventions EMA** (commentées en tête de `strategy_orb.py` et `strategy_overnight.py`) : ORB compare le dernier close CONFIRMÉ (hier) ; Overnight inclut le close du jour (15:55). Divergence subtile backtest/live à vérifier.
3. **`broker_ib.py`** `place_market`/`place_bracket` : mapping direction→BUY/SELL, DRY_RUN.
4. **`main.py`** séquencement ORB→Overnight + heartbeat.

## ⚠️ Caveats connus (déjà identifiés, à confirmer en audit)
- **SIZING $10k sur-levé** : backtest portefeuille intégré montre qu'à 1 MNQ + 1 MES sur $10k (= 6× levier), le kill switch −20% se déclenche dès mars 2021 → stratégie tuée. **Sizing viable = pool ~$30k (2×)** : +6.4%/an, Sharpe 1.15, MDD −13.5%, pas de halt. → `CAPITAL_BASE` à passer à 30000 avant GO (changement de config, pas de code). Détail : `stack-combo-portfolio.md`.
- **Détection fill réel (live)** : l'exit ORB infère TARGET/STOP via bars 1-min ; en live le bracket IB gère réellement — à rapprocher des fills IB. DRY_RUN simule fidèlement.
- **`observed_slippage_ticks`** : champ loggé mais pas encore branché sur (prix-signal − prix-fill) réel.

## Prérequis avant lancement
1. Phase C (re-run ORB sur CME réel via IB) doit PASS.
2. IB Gateway loggé (port 4002 paper, account DUQ700122) — cf. `IB_SETUP.md`.
3. Lancer en `DRY_RUN=True` 1-2 jours, puis paper réel.

Secrets (`ib_demo.json`) dans `~/.openclaw/secrets/` — **hors repo** (gitignored).
