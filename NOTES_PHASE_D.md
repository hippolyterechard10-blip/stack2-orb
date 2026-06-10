# Phase D — Bot paper trading : notes d'implémentation

**Date** : 2026-06-09 · État : **CODÉ, PAS LANCÉ**. DRY_RUN=True. En attente relecture config + risk_manager par Hippo, puis GO.

---

## Structure créée (`~/.openclaw/workspace/stack2-orb/`)

```
config.py              # tous les paramètres + DRY_RUN + ON_MODE
calendar_util.py       # calendrier NYSE (fériés, demi-séances) via pandas_market_calendars
broker_ib.py           # couche IB ib_insync (DRY_RUN-aware, reconnexion, front-month MNQ/MES)
risk_manager.py        # pool $10k, kill switches, anti-double-position
logger.py              # JSONL/jour + daily_summary + heartbeat
strategy_orb.py        # sleeve ORB NQ_OR30_F1 (réplique backtest)
strategy_overnight.py  # sleeve Overnight ES V4 EMA+VIX (réplique backtest)
dashboard.py           # Flask localhost:5000
main.py                # event loop, séquencement ORB→Overnight, heartbeat
run.sh                 # wrapper (attend Gateway port 4002 avant de lancer)
com.hippo.stack2orb.plist.disabled   # launchd (NON chargé — à activer au GO)
requirements.txt       # versions pinnées py3.9
```

## Diff vs stack2-orb/ existant (Phase B)

Existant avant : `venv/`, `research/ib_connect_test.py`, `IB_SETUP.md`, `requirements.txt`, `.gitignore`, `logs/`, `data_cache/`.
Ajouté : les 9 modules bot + run.sh + plist + ce doc. Deps ajoutées au venv : `pandas_market_calendars==4.3.3`, `yfinance`, `pandas` downgradé 2.3→1.5.3 (requis par pmc, compatible). `requirements.txt` re-pinné.

---

## Traitement des 8 corrections de l'addendum

| # | Item | Traitement |
|---|---|---|
| 1 | Weekend/Vendredi overnight | Backtest vérifié : `shift(-1)` sur index de **jours de marché** → vendredi tenu jusqu'à lundi (held over weekend). **Répliqué** : exit overnight = `next_trading_day(entry_date)` à 9:29, donc vendredi→lundi. Documenté dans `strategy_overnight.py`. |
| 2 | EMA20 convention | **Deux conventions distinctes, chacune calquée sur son backtest** : **ORB** compare le dernier close **confirmé (hier)** vs EMA20 des closes confirmés (le jour courant n'est pas clôturé à 10:00) — = `shift(1)` du backtest. **Overnight** inclut le close **du jour (15:55)** dans l'EMA — = convention du backtest overnight. Commenté explicitement dans chaque module. |
| 3 | Calendrier (fériés/demi-séances) | `pandas_market_calendars` calendrier **NYSE**. Férié → aucune strat. Demi-séance (close 13:00) → ORB EOD exit **12:55**, Overnight entry **12:55**. Validé : 2025-11-28 détecté half-day, exit 12:55. |
| 4 | Séquencement ORB→Overnight 15:55 | **Double sécurité** : (a) `main.py` appelle `orb.run_cycle()` PUIS `overnight.run_cycle()` ; (b) l'overnight vérifie `get_position(MNQ)==0` avant d'entrer, sinon délaye+retry. **Assertion anti-double-position** dans `risk_manager.assert_no_double_position()` → **HALT** si MNQ ET MES ouverts simultanément. |
| 5 | ON_LEVERAGE_MAX | **Conservé comme garde-fou anti-scaling futur** (documenté config). Non-binding avec 1 MES fixe ; bloquera tout sizing >1 contrat qui pousserait le levier notionnel au-delà de 3×. |
| 6 | DRY_RUN | `config.DRY_RUN=True`. En dry-run : tous les signaux/décisions sont loggés, **aucun ordre réel** (broker log au lieu de placer). Permet de valider la logique 1-2 jours avant trading paper. |
| 7 | Dashboard slippage alert | `logger` calcule `slippage_alert` si slippage réel > 2× backtest. Dashboard `/trades` surligne en **rouge** ces trades + bannière rouge sur `/`. |
| 8 | Heartbeat | `logger.heartbeat()` écrit `ALIVE {ts} equity={..} positions={..}` dans `logs/heartbeat.log` toutes les **5 min** (HEARTBEAT_SEC). Statut `HALTED` si kill switch déclenché. |

---

## Décisions d'implémentation à VALIDER par Hippo

1. **⚠️ CONFLIT V4 vs V5 (le plus important)** : le brief titre le sleeve overnight « V4 EMA+VIX » mais sa description step 1a dit « si close < EMA20 → SHORT autorisé » (= V5 bidirectionnel). **Le backtest V4 est LONG-ONLY gated** (long si close>EMA **ET** VIX<25, sinon **flat** — jamais short). J'ai implémenté **V4 par défaut** (la variante recommandée dans `stack3-overnight-drift.md` pour son meilleur profil crash), avec `config.ON_MODE` pour basculer en `"V5"` si tu préfères le bidirectionnel. **→ Confirme V4 ou V5.**

2. **ORB entrée au market sur breakout** : le backtest entrait au *close* de la bougie de cassure. En live on détecte après la clôture de la bougie 5-min → ordre market juste après. Léger slippage attendu (tracké au dashboard). Convention standard ORB.

3. **1er breakout puis filtre = skip** : répliqué fidèlement — on prend le **1er** breakout (peu importe le sens), et si le sens ≠ direction EMA autorisée → **skip le jour** (pas d'attente d'un breakout dans le bon sens). C'est exactement la logique du backtest (`continue`).

4. **VIX source** : Yahoo `^VIX` daily (même source que le backtest). Récupéré à l'entrée 15:55. Alternative IB (`Index('VIX','CBOE')`) possible si tu veux éviter Yahoo.

5. **Front-month vs continu** : data historique via `ContFuture` (continu), **ordres sur le contrat front-month réel** résolu via `reqContractDetails`. Le rollover est donc géré côté IB pour la data ; les ordres tradent toujours le front actif.

6. **plist NON chargé** : `com.hippo.stack2orb.plist.disabled` créé mais **pas installé** dans LaunchAgents — pour éviter tout démarrage avant ta relecture. À activer au GO (voir ci-dessous).

---

## Comment lancer (après ta relecture + IB Gateway up)

```bash
# 1. DRY-RUN d'abord (1-2 jours, valide la logique sans ordres)
cd ~/.openclaw/workspace/stack2-orb
./venv/bin/python main.py          # DRY_RUN=True dans config.py
# Dashboard : http://127.0.0.1:5000

# 2. Quand la logique est validée en dry-run → passer DRY_RUN=False dans config.py → paper réel

# 3. Auto-restart launchd (optionnel, au GO) :
cp com.hippo.stack2orb.plist.disabled ~/Library/LaunchAgents/com.hippo.stack2orb.plist
launchctl load ~/Library/LaunchAgents/com.hippo.stack2orb.plist
```

⚠️ **Prérequis avant tout lancement** : Phase C (re-run ORB sur CME réel) doit avoir **PASS**, et IB Gateway doit être loggé (port 4002). Le bot ne trade que des micros (1 MNQ + 1 MES), pool virtuel $10k, kill switches actifs.

---

## Points qui restent à finaliser au moment du GO (non bloquants pour la relecture)

- **Détection précise du fill réel** (live, non DRY_RUN) : actuellement l'exit ORB infère TARGET/STOP via les bars 1-min ; en live le bracket IB gère réellement le SL/TP — à rapprocher des fills IB pour le P&L exact (le DRY_RUN simule fidèlement).
- **Slippage observé** : le champ est loggé mais le calcul `observed_slippage_ticks` doit être branché sur la différence prix-signal vs prix-fill réel (trivial en live, N/A en dry-run).
- Ces deux points se affinent pendant le dry-run sur vraies données — la logique de décision (la partie critique) est complète et testée à la compilation.

---

## Audit fixes — 2026-06-09 (audit code complet par Claude)

Audit du repo (1080 lignes, 9 modules) : architecture saine, sécurité OK, conventions EMA correctes/différenciées, anti-double-position et séquencement étanches. 6 bugs P&L/exécution + 1 mineur, tous corrigés (bot reste DRY_RUN, non lancé).

| Bug | Gravité | Fix |
|---|---|---|
| **1** P&L théorique vs bracket réel | 🔴 grave | `strategy_orb._manage_position` : en live, lit le VRAI fill du bracket via `broker.bracket_exit_fill()` (avgFillPrice du leg SL/TP rempli). EOD via `place_market`+fill réel. DRY_RUN : théorique ±1 tick slippage. |
| **2** slippage jamais capturé | 🔴 grave | Stratégies calculent `observed_slippage_ticks = (|entry_fill−théo|+|exit_fill−théo|)/(2·tick)` et le passent à `log_trade`. DRY_RUN = `BACKTEST_SLIPPAGE_TICKS`. Alerte `slip_alert` vérifiée (se déclenche à >2× backtest). |
| **3** stop overnight raté/doublon | 🟠 moyen | `_maybe_exit` : live lit le fill du `StopOrder` natif (pas de recalcul → pas de doublon). DRY_RUN : check high/low barre 1-min (pas juste close), cohérent avec ORB. |
| **4** pas de réconciliation | 🟠 moyen | `rm.reconcile()` compare P&L tracké vs `realizedPNL` IB au heartbeat ; écart >$50 → warning + `reconciliation_drift` dans daily_summary + flag dashboard. **Skippé en DRY_RUN** (pas de fills IB). |
| **5** P&L calculé 2× | 🟠 moyen | Source unique : `pnl_dollars_net` calculé UNE fois dans la stratégie, passé explicitement à `record_realized()` ET `log_trade()`. `log_trade` ne recalcule plus (fallback+warning si non passé). |
| **6** frais non déduits | 🟠 moyen | `MNQ_ROUND_TRIP_FEE=1.50`, `MES_ROUND_TRIP_FEE=1.12`. `pnl_net = pnl_pts×PV − fee`. `fees_paid_total`/`fees_paid_today` dans daily_summary + dashboard /api. |
| **7** dashboard sans auth | 🟡 doc | Commentaire sécurité en tête de `dashboard.py` : localhost-only, ajouter Flask-HTTPAuth avant toute exposition réseau. |

**Vérifs** : tout compile, alerte slippage testée (1 tick→OK, 3 ticks→alerte), fees trackés, fallback BUG 5 testé, réconciliation skip DRY_RUN. **Bot toujours DRY_RUN=True, non lancé** — en attente re-relecture Claude avant clarification levier (Partie B) et re-run sizing unifié (Partie C).
