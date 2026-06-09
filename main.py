"""
Stack #2/#3 paper bot — event loop principal.
Pool unique $10k. Séquencement strict ORB(jour) → Overnight(nuit) à 15:55 (addendum #4).
ib_insync gère son propre event loop → boucle synchrone avec ib.sleep (idiomatique ib_insync).

Lancer (après IB Gateway up + relecture config) :
    cd ~/.openclaw/workspace/stack2-orb && ./venv/bin/python main.py
DRY_RUN=True par défaut (config.py) : log signaux/décisions SANS ordres réels.
"""
import logging, threading, datetime as dt
import pytz

import config, logger, calendar_util as cal
from broker_ib import IBBroker
from risk_manager import RiskManager
from strategy_orb import ORBStrategy
from strategy_overnight import OvernightStrategy
import dashboard

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[logging.FileHandler("logs/bot.log"), logging.StreamHandler()])
log = logging.getLogger("main")
ET = pytz.timezone(config.TIMEZONE)


def now_et():
    return dt.datetime.now(ET)


def main():
    log.info("═══ Stack #2/#3 bot — DÉMARRAGE (DRY_RUN=%s, ON_MODE=%s) ═══",
             config.DRY_RUN, config.ON_MODE)
    broker = IBBroker()
    try:
        broker.connect()
    except Exception as e:
        log.error("Connexion IB impossible au démarrage : %s. IB Gateway lancé/loggé ? Halt.", e)
        return

    rm = RiskManager(broker)
    orb = ORBStrategy(broker, rm)
    overnight = OvernightStrategy(broker, rm)

    # Dashboard (thread séparé, localhost uniquement)
    dashboard.bind(broker, rm, orb, overnight)
    threading.Thread(target=dashboard.start_dashboard, daemon=True).start()

    last_hb = 0.0
    import time as _t
    while True:
        try:
            if not broker.ensure_connected():
                log.error("HALT — connexion IB définitivement perdue.")
                break
            now = now_et()

            if cal.is_trading_day(now.date()) or _prev_day_was_market(now):
                # ── Séquencement strict : ORB d'abord (gère son EOD exit), Overnight ensuite ──
                # (Overnight vérifie en plus que MNQ est FLAT avant d'entrer — double sécurité.)
                orb.run_cycle(now)
                overnight.run_cycle(now)

            # Heartbeat (addendum #8)
            if _t.time() - last_hb >= config.HEARTBEAT_SEC:
                q_orb, _ = broker.get_position(config.ORB_SYMBOL)
                q_on, _ = broker.get_position(config.ON_SYMBOL)
                logger.heartbeat(rm.virtual_equity(),
                                 {"MNQ": q_orb, "MES": q_on},
                                 status="HALTED" if rm.halted else "ALIVE")
                _write_summary(rm, orb, overnight)
                last_hb = _t.time()

            if rm.halted:
                log.error("🛑 HALT actif : %s — boucle en veille (pas de trade).", rm.halt_reason)

        except Exception as e:
            log.exception("Erreur boucle principale : %s", e)
        broker.ib.sleep(config.LOOP_SLEEP_SEC)


def _prev_day_was_market(now):
    """Vrai si on est tôt le matin (avant 9:30) un jour de marché OU le lendemain d'un jour de
    marché — pour gérer l'exit overnight (peut tomber un lundi après un vendredi)."""
    return cal.prev_trading_day(now.date()) is not None and now.time() < dt.time(10, 0)


def _write_summary(rm, orb, overnight):
    trades = logger.read_all_trades()
    orb_tr = [t for t in trades if t["sleeve"] == "ORB"]
    on_tr = [t for t in trades if t["sleeve"] == "OVERNIGHT"]
    def wr(ts):
        w = [t for t in ts if t["pnl_dollars"] > 0]
        return round(100*len(w)/len(ts), 1) if ts else None
    logger.write_daily_summary({
        "virtual_equity": rm.virtual_equity(), "realized_pnl": rm.realized_pnl,
        "daily_pnl": rm.daily_pnl(), "halted": rm.halted,
        "n_trades_orb": len(orb_tr), "n_trades_overnight": len(on_tr),
        "winrate_orb": wr(orb_tr), "winrate_overnight": wr(on_tr),
        "orb_state": orb.state(), "overnight_state": overnight.state(),
        "dry_run": config.DRY_RUN,
    })


if __name__ == "__main__":
    main()
