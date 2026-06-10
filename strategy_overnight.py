"""
Sleeve 2 — Overnight ES V4 EMA+VIX (réplique du backtest overnight_drift_backtest.py).

Logique (jours de marché US) :
  Entry à 15:55 ET (12:55 demi-séance). Held overnight, exit à l'open du PROCHAIN jour de
    marché (9:29 ET). ⚠️ Vendredi → tenu jusqu'à lundi (weekend), conforme au backtest
    (shift sur index de jours de marché, addendum #1).
  Filtre EMA20 daily — convention backtest (addendum #2) : le close du JOUR (15:55) est INCLUS
    dans l'EMA. long_ok = close_today(15:55) > EMA20(closes incl. aujourd'hui).
  Mode V4 (défaut) = LONG-ONLY gated : trade LONG seulement si long_ok ET VIX<25 ; sinon FLAT.
    (Mode V5 optionnel = bidirectionnel : short si close<EMA, sans gate VIX.)
  Stop = 2× ATR(14) daily depuis l'entrée. Pas de TP (exit à l'open).
  Séquencement (addendum #4) : n'entre QUE si la position ORB (MNQ) est confirmée FLAT.
"""
import datetime as dt
import logging
import pandas as pd
import config, calendar_util as cal, logger

log = logging.getLogger("overnight")


def get_vix_close():
    """VIX close du jour via Yahoo ^VIX (même source que le backtest)."""
    try:
        import yfinance as yf
        v = yf.download("^VIX", period="5d", interval="1d", progress=False, auto_adjust=False)
        if hasattr(v.columns, "get_level_values"):
            v.columns = v.columns.get_level_values(0)
        return float(v["Close"].dropna().iloc[-1])
    except Exception as e:
        log.warning("VIX indispo (%s) — par sécurité on considère VIX élevé (skip).", e)
        return 999.0


class OvernightStrategy:
    def __init__(self, broker, rm):
        self.b = broker
        self.rm = rm
        self.sym = config.ON_SYMBOL
        self.in_pos = False
        self.entry = self.stop = None
        self.entry_theo = None         # prix de signal (pour mesurer le slippage)
        self.stop_trade = None         # StopOrder natif (live) — lu au lieu de recalculer (BUG 3)
        self.pos_dir = 0
        self.entry_date = None
        self.evaluated_date = None     # date dont l'entrée overnight a déjà été évaluée

    # ── Indicateurs ──────────────────────────────────────────────────────────
    def _ema_and_atr(self):
        d = self.b.get_daily_closes(self.sym, max(config.ON_EMA_PERIOD, config.ON_ATR_PERIOD) + 10)
        if d is None or len(d) < config.ON_EMA_PERIOD:
            return None, None, None
        closes = d["close"].astype(float)
        cur = self.b.last_price(self.sym)   # proxy du close 15:55 (jour en cours)
        # EMA INCL. aujourd'hui : on append le prix courant (15:55) aux closes confirmés
        closes_incl = pd.concat([closes, pd.Series([cur])], ignore_index=True)
        ema = closes_incl.ewm(span=config.ON_EMA_PERIOD, adjust=False).mean().iloc[-1]
        # ATR(14) daily sur les barres confirmées
        h, l, c = d["high"].astype(float), d["low"].astype(float), d["close"].astype(float)
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(config.ON_ATR_PERIOD).mean().iloc[-1]
        return cur, ema, atr

    # ── Cycle ────────────────────────────────────────────────────────────────
    def run_cycle(self, now):
        # 1) Exit du matin si en position et nouveau jour de marché atteint
        if self.in_pos:
            self._maybe_exit(now)
            return

        today = now.date()
        if not cal.is_trading_day(today):
            return
        if self.evaluated_date == today:
            return  # entrée déjà évaluée aujourd'hui (tradée ou skippée)

        entry_t = cal.overnight_entry_et(today)
        t = now.time()
        # fenêtre d'entrée : [entry_t, entry_t + 5min]
        if not (entry_t <= t < (dt.datetime.combine(today, entry_t) + dt.timedelta(minutes=5)).time()):
            return

        # 2) Séquencement (addendum #4) : ORB doit être FLAT avant d'entrer
        q_orb, _ = self.b.get_position(config.ORB_SYMBOL)
        if q_orb != 0:
            log.info("Overnight: ORB encore en position (%s) — on délaye l'entrée, retry.", q_orb)
            return  # on retentera au prochain tick (l'EOD ORB est à 15:55 aussi)

        self.evaluated_date = today
        if not self.rm.can_trade("Overnight"):
            return

        cur, ema, atr = self._ema_and_atr()
        if cur is None or atr is None:
            log.warning("Overnight: indicateurs indispo, skip.")
            return
        long_ok = cur > ema
        vix = get_vix_close()

        # 3) Décision selon le mode
        if config.ON_MODE == "V4":
            if long_ok and vix < config.ON_VIX_MAX:
                self._enter(1, cur, atr, now, vix)
            else:
                log.info("Overnight V4 SKIP : long_ok=%s VIX=%.1f (seuil %s)",
                         long_ok, vix, config.ON_VIX_MAX)
        elif config.ON_MODE == "V5":
            self._enter(1 if long_ok else -1, cur, atr, now, vix)

    def _enter(self, direction, price, atr, now, vix):
        slip = config.BACKTEST_SLIPPAGE_TICKS * config.ON_TICK
        stop = price - config.ON_ATR_STOP_MULT * atr if direction > 0 \
            else price + config.ON_ATR_STOP_MULT * atr
        res = self.b.place_market(self.sym, config.ON_MAX_CONTRACTS, direction)
        entry_fill = (price + direction * slip) if config.DRY_RUN else (res.get("fill") or price)
        # stop natif (live) — son fill sera LU au lieu de recalculer (BUG 3)
        self.stop_trade = None
        if not config.DRY_RUN:
            from ib_insync import StopOrder
            so = StopOrder("SELL" if direction > 0 else "BUY", config.ON_MAX_CONTRACTS, stop)
            self.stop_trade = self.b.ib.placeOrder(self.b.front(self.sym), so)
        self.in_pos = True
        self.entry_theo = price
        self.entry, self.stop, self.pos_dir, self.entry_date = entry_fill, stop, direction, now.date()
        log.info("ENTRÉE Overnight %s théo@%.2f fill@%.2f SL(2×ATR)=%.2f VIX=%.1f",
                 "LONG" if direction > 0 else "SHORT", price, entry_fill, stop, vix)

    def _maybe_exit(self, now):
        slip = config.BACKTEST_SLIPPAGE_TICKS * config.ON_TICK
        nxt = cal.next_trading_day(self.entry_date)
        morning = nxt is not None and now.date() >= nxt and now.time() >= dt.time(9, 29)
        reason = exit_theo = exit_fill = None

        if not config.DRY_RUN:
            # stop natif déclenché ? → lire SON fill (PAS de recalcul → évite le doublon, BUG 3)
            if self.stop_trade is not None and self.stop_trade.orderStatus.status == "Filled":
                exit_fill = float(self.stop_trade.orderStatus.avgFillPrice)
                exit_theo, reason = self.stop, "STOP"
            elif morning:
                res = self.b.place_market(self.sym, config.ON_MAX_CONTRACTS, -self.pos_dir)
                self.b.cancel_all(self.sym)
                exit_fill = res.get("fill"); exit_theo = self.b.last_price(self.sym) or exit_fill
                reason = "OPEN"
            else:
                return
        else:
            # DRY_RUN : stop checké sur high/low de la barre 1-min (BUG 3 : pas juste close)
            bars = self.b.get_bars(self.sym, 1, 3)
            last = float(bars["close"].iloc[-1]) if bars is not None and len(bars) else self.entry
            hi = float(bars["high"].iloc[-1]) if bars is not None and len(bars) else last
            lo = float(bars["low"].iloc[-1]) if bars is not None and len(bars) else last
            if self.pos_dir > 0 and lo <= self.stop:
                reason, exit_theo = "STOP", self.stop
            elif self.pos_dir < 0 and hi >= self.stop:
                reason, exit_theo = "STOP", self.stop
            elif morning:
                reason, exit_theo = "OPEN", last
                self.b.place_market(self.sym, config.ON_MAX_CONTRACTS, -self.pos_dir)
                self.b.cancel_all(self.sym)
            if reason is None:
                return
            exit_fill = exit_theo - self.pos_dir * slip

        # P&L NET calculé UNE fois (BUG 5) : fills (slippage) − commission (BUG 6)
        pnl_pts = (exit_fill - self.entry) * self.pos_dir
        fee = config.MES_ROUND_TRIP_FEE
        pnl_usd_net = pnl_pts * config.ON_POINT_VALUE - fee
        self.rm.record_realized(pnl_usd_net, sleeve="OVERNIGHT", fee=fee)   # source unique
        if config.DRY_RUN:
            obs = config.BACKTEST_SLIPPAGE_TICKS
        else:
            obs = (abs(self.entry - (self.entry_theo or self.entry))
                   + abs(exit_fill - (exit_theo or exit_fill))) / (2 * config.ON_TICK)
        logger.log_trade("OVERNIGHT", self.sym, self.pos_dir, self.entry, exit_fill, reason,
                         pnl_dollars=pnl_usd_net, fees=fee, virtual_equity=self.rm.virtual_equity(),
                         observed_slippage_ticks=round(obs, 2), tick=config.ON_TICK)
        self.in_pos = False

    def state(self):
        return {"in_pos": self.in_pos, "pos_dir": self.pos_dir,
                "entry": self.entry, "stop": self.stop, "entry_date": str(self.entry_date)}
