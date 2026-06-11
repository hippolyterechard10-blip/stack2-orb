"""
Risk manager — pool unique $10k virtuel, kill switches globaux (les 2 sleeves partagent).
P&L virtuel tracké sur base $10k via le P&L réalisé cumulé (PAS le solde IB Demo $1M).

Kill switches :
  - equity virtuelle < KILL_EQUITY ($8000, MDD -20%) → arrêt TOTAL des 2 sleeves (halt)
  - perte virtuelle du jour < -DAILY_LOSS_MAX (-$400, -4%) → plus aucun nouveau trade ce jour
Assertion anti-double-position (addendum #4) : halt si MNQ ET MES ouverts simultanément.
"""
import datetime as dt
import logging
import config
import logger

log = logging.getLogger("risk")


class RiskManager:
    def __init__(self, broker):
        self.broker = broker
        self.realized_pnl = 0.0          # P&L réalisé cumulé total (virtuel, $)
        self.current_day = dt.date.today()
        self.halted = False              # kill switch global déclenché
        self.halt_reason = None
        # P&L réalisé PAR SLEEVE au début du jour (pour daily stops par sleeve)
        self.day_start_by_sleeve = {"ORB": 0.0, "OVERNIGHT": 0.0}
        self.realized_by_sleeve = {"ORB": 0.0, "OVERNIGHT": 0.0}
        # Réconciliation virtuel vs IB réel (BUG 4)
        self.reconcile_warning = False
        self.reconcile_diff = 0.0
        # Frais cumulés (BUG 6)
        self.fees_paid = 0.0
        self.day_start_fees = 0.0
        # BUG E : reconstruire l'état depuis les logs (kill switch ré-armé au restart)
        self.reload_state()
        # baseline réconciliation = état au démarrage de CETTE session (restart-safe)
        self.session_start_realized = self.realized_pnl

    def reload_state(self):
        """BUG E : reconstruit realized_pnl / par sleeve / fees / baselines du jour depuis
        les trades loggés. Sans ça, un restart (launchd KeepAlive) remet l'equity à $10k et
        désarme le kill switch -20% alors que les pertes réelles persistent."""
        import logger as _logger
        trades = _logger.read_all_trades()
        today = dt.date.today()
        self.realized_pnl = 0.0; self.fees_paid = 0.0
        self.realized_by_sleeve = {"ORB": 0.0, "OVERNIGHT": 0.0}
        self.day_start_by_sleeve = {"ORB": 0.0, "OVERNIGHT": 0.0}
        self.day_start_fees = 0.0
        for t in trades:
            sl = "OVERNIGHT" if str(t.get("sleeve", "")).upper().startswith("OVER") else "ORB"
            pnl = float(t.get("pnl_dollars", 0) or 0); fee = float(t.get("fees", 0) or 0)
            self.realized_pnl += pnl; self.fees_paid += fee
            self.realized_by_sleeve[sl] += pnl
            try:
                tdate = dt.datetime.fromisoformat(t["timestamp"]).date()
            except Exception:
                tdate = today
            if tdate < today:                       # baseline = trades AVANT aujourd'hui
                self.day_start_by_sleeve[sl] += pnl
                self.day_start_fees += fee
        if trades:
            log.info("État rechargé : equity virtuelle $%.2f, %d trades, daily P&L $%.2f, fees $%.2f",
                     self.virtual_equity(), len(trades), self.daily_pnl(), self.fees_paid)
            if self.virtual_equity() < config.KILL_EQUITY:   # déjà sous le seuil au boot
                self.halted = True
                self.halt_reason = f"KILL SWITCH (rechargé au boot) : equity ${self.virtual_equity():.0f}"
                log.error("🛑 %s", self.halt_reason)

    # ── Equity virtuelle ─────────────────────────────────────────────────────
    def virtual_equity(self):
        return config.CAPITAL_BASE + self.realized_pnl

    def record_realized(self, pnl_dollars, sleeve="ORB", fee=0.0):
        """pnl_dollars = P&L NET (déjà net de frais, calculé dans la stratégie — source unique)."""
        self.realized_pnl += pnl_dollars
        self.fees_paid += fee
        key = "OVERNIGHT" if str(sleeve).upper().startswith("OVER") else "ORB"
        self.realized_by_sleeve[key] = self.realized_by_sleeve.get(key, 0.0) + pnl_dollars

    def _roll_day(self):
        today = dt.date.today()
        if today != self.current_day:
            self.current_day = today
            self.day_start_by_sleeve = dict(self.realized_by_sleeve)
            self.day_start_fees = self.fees_paid

    def fees_today(self):
        return self.fees_paid - self.day_start_fees

    def daily_pnl(self):
        return sum(self.realized_by_sleeve.values()) - sum(self.day_start_by_sleeve.values())

    def sleeve_daily_pnl(self, sleeve):
        key = "OVERNIGHT" if str(sleeve).upper().startswith("OVER") else "ORB"
        return self.realized_by_sleeve.get(key, 0.0) - self.day_start_by_sleeve.get(key, 0.0)

    # ── Anti double-position (addendum #4) ──────────────────────────────────
    def assert_no_double_position(self):
        q_orb, _ = self.broker.get_position(config.ORB_SYMBOL)
        q_on, _ = self.broker.get_position(config.ON_SYMBOL)
        if q_orb != 0 and q_on != 0:
            self.halted = True
            self.halt_reason = (f"DOUBLE POSITION détectée : {config.ORB_SYMBOL}={q_orb} "
                                f"ET {config.ON_SYMBOL}={q_on} — HALT immédiat (double marge interdite)")
            log.error("🛑 %s", self.halt_reason)
            return False
        return True

    # ── Gate principal avant chaque trade ────────────────────────────────────
    def can_trade(self, sleeve_name) -> bool:
        self._roll_day()
        if self.halted:
            log.warning("HALTED (%s) — %s refusé", self.halt_reason, sleeve_name)
            return False
        if not self.assert_no_double_position():
            return False
        # Kill switch GLOBAL : -20% equity → halt total des 2 sleeves
        eq = self.virtual_equity()
        if eq < config.KILL_EQUITY:
            self.halted = True
            self.halt_reason = f"KILL SWITCH : equity virtuelle ${eq:.0f} < ${config.KILL_EQUITY}"
            log.error("🛑 %s — arrêt total des 2 sleeves", self.halt_reason)
            return False
        # Daily stop PAR SLEEVE (pas global → préserve la décorrélation)
        key = "OVERNIGHT" if str(sleeve_name).upper().startswith("OVER") else "ORB"
        cap = config.ORB_DAILY_LOSS_MAX if key == "ORB" else config.ON_DAILY_LOSS_MAX
        if cap is not None and self.sleeve_daily_pnl(sleeve_name) < -cap:
            log.warning("DAILY STOP %s atteint (${:.0f} < -${}) — pas de nouveau trade %s ce jour",
                        key, self.sleeve_daily_pnl(sleeve_name), cap, sleeve_name)
            return False
        return True

    def reconcile(self, ib_session_realized):
        """BUG 4 + ajustement round 3 : croiser le P&L de la SESSION COURANTE des deux côtés —
        tracked depuis le boot (realized_pnl − session_start_realized) vs IB realizedPNL de session.
        PAS les cumuls totaux : IB session repart de 0 à chaque restart alors que le tracked est
        cumulatif (BUG E) → comparer les cumuls donnerait un faux warning après chaque restart."""
        if ib_session_realized is None:
            return  # indispo (DRY_RUN ou pas de fills)
        tracked_session = self.realized_pnl - self.session_start_realized
        self.reconcile_diff = tracked_session - ib_session_realized
        if abs(self.reconcile_diff) > config.RECONCILE_THRESHOLD_USD:
            self.reconcile_warning = True
            log.warning("⚠️ RÉCONCILIATION : P&L session tracké $%.2f vs IB session $%.2f — écart $%.2f > $%s",
                        tracked_session, ib_session_realized, self.reconcile_diff,
                        config.RECONCILE_THRESHOLD_USD)
        else:
            self.reconcile_warning = False

    def status(self):
        return {"virtual_equity": round(self.virtual_equity(), 2),
                "realized_pnl": round(self.realized_pnl, 2),
                "daily_pnl": round(self.daily_pnl(), 2),
                "halted": self.halted, "halt_reason": self.halt_reason,
                "reconcile_warning": self.reconcile_warning,
                "reconcile_diff": round(self.reconcile_diff, 2),
                "fees_paid": round(self.fees_paid, 2),
                "mdd_kill_at": config.KILL_EQUITY}
