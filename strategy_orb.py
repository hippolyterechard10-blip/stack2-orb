"""
Sleeve 1 — ORB NQ_OR30_F1 (réplique exacte du backtest orb_es_nq_backtest.py).

Logique (jours de marché US) :
  9:30-10:00 ET : Opening Range (OR_HIGH/OR_LOW sur 1-min bars).
  Filtre EMA20 daily — convention backtest (addendum #2) : compare le DERNIER close daily
    CONFIRMÉ (= hier, le jour courant n'est pas clôturé à 10:00) vs EMA20 des closes confirmés.
    close[hier] > EMA20 → LONG autorisé ; sinon SHORT autorisé.
  Après 10:00 : on prend le 1ER breakout 5-min (close hors OR, peu importe le sens). PUIS si
    le sens du breakout NE matche PAS la direction autorisée → SKIP le jour (pas de trade).
    (C'est exactement la logique du backtest : premier breakout, puis filtre, sinon `continue`.)
  Entrée market. Stop = OR opposite. Target = niveau de cassure ± 1× OR_WIDTH.
  EOD exit (15:55, ou 12:55 demi-séance) si encore en position. Max 1 trade/jour.
"""
import datetime as dt
import logging
import pandas as pd
import config, calendar_util as cal, logger

log = logging.getLogger("orb")


class ORBStrategy:
    def __init__(self, broker, rm):
        self.b = broker
        self.rm = rm
        self.sym = config.ORB_SYMBOL
        self._reset(dt.date.today())

    def _reset(self, day):
        self.day = day
        self.or_built = False
        self.or_high = self.or_low = self.or_width = None
        self.dir_allowed = None        # +1 long / -1 short
        self.breakout_evaluated = False
        self.in_pos = False
        self.entry = self.stop = self.target = None
        self.entry_theo = None         # prix de signal (pour mesurer le slippage)
        self.bracket = None            # résultat place_bracket (Trade objects en live)
        self.pos_dir = 0
        self.done = False

    # ── Filtre EMA20 (dernier close confirmé = hier) ─────────────────────────
    def _ema_direction(self):
        d = self.b.get_daily_closes(self.sym, config.ORB_EMA_PERIOD + 5)
        if d is None or len(d) < config.ORB_EMA_PERIOD:
            return None
        closes = d["close"].astype(float)
        ema = closes.ewm(span=config.ORB_EMA_PERIOD, adjust=False).mean()
        # dernier close CONFIRMÉ vs son EMA (le jour courant n'est pas dans `d` à 10:00)
        return 1 if closes.iloc[-1] > ema.iloc[-1] else -1

    def _build_or(self, now):
        bars = self.b.get_bars(self.sym, 1, 60)
        if bars is None or not len(bars):
            return False
        bars = bars.copy()
        bars["et"] = pd.to_datetime(bars["date"]).dt.tz_convert(config.TIMEZONE) \
            if pd.to_datetime(bars["date"]).dt.tz is not None \
            else pd.to_datetime(bars["date"]).dt.tz_localize(config.TIMEZONE)
        win = bars[(bars["et"].dt.time >= dt.time(9, 30)) & (bars["et"].dt.time < dt.time(10, 0))
                   & (bars["et"].dt.date == now.date())]
        if len(win) < 3:
            return False
        self.or_high = float(win["high"].max())
        self.or_low = float(win["low"].min())
        self.or_width = self.or_high - self.or_low
        self.dir_allowed = self._ema_direction()
        self.or_built = self.or_width > 0 and self.dir_allowed is not None
        if self.or_built:
            log.info("OR construit : H=%.2f L=%.2f W=%.2f dir_allowed=%s",
                     self.or_high, self.or_low, self.or_width,
                     "LONG" if self.dir_allowed > 0 else "SHORT")
        return self.or_built

    def _first_breakout(self, now):
        """1ère bougie 5-min (close) hors OR depuis 10:00. Retourne +1/-1/0."""
        bars = self.b.get_bars(self.sym, config.ORB_ENTRY_TF, 80)
        if bars is None or not len(bars):
            return 0
        bars = bars.copy()
        et = pd.to_datetime(bars["date"])
        et = et.dt.tz_convert(config.TIMEZONE) if et.dt.tz is not None else et.dt.tz_localize(config.TIMEZONE)
        bars["et"] = et
        rest = bars[(bars["et"].dt.time >= dt.time(10, 0)) & (bars["et"].dt.date == now.date())]
        for _, r in rest.iterrows():
            c = float(r["close"])
            if c > self.or_high:
                return 1
            if c < self.or_low:
                return -1
        return 0

    # ── Cycle ────────────────────────────────────────────────────────────────
    def run_cycle(self, now):
        if now.date() != self.day:
            self._reset(now.date())
        if self.done or not cal.is_trading_day(now.date()):
            return
        eod = cal.orb_eod_exit_et(now.date())
        t = now.time()

        # gestion position ouverte (stop/target/EOD)
        if self.in_pos:
            self._manage_position(now, eod)
            return

        if t < dt.time(10, 0):
            return  # OR pas complète
        if not self.or_built and not self._build_or(now):
            return
        if self.breakout_evaluated or t >= eod:
            if t >= eod:
                self.done = True
            return

        bk = self._first_breakout(now)
        if bk == 0:
            return  # pas encore de breakout
        self.breakout_evaluated = True   # 1er breakout évalué (qu'on trade ou non)
        if bk != self.dir_allowed:
            log.info("Breakout %s ≠ dir autorisée %s → SKIP jour (logique backtest)",
                     "LONG" if bk > 0 else "SHORT", "LONG" if self.dir_allowed > 0 else "SHORT")
            self.done = True
            return
        if not self.rm.can_trade("ORB"):
            self.done = True
            return
        self._enter(bk, now)

    def _enter(self, direction, now):
        px = self.b.last_price(self.sym)
        slip = config.BACKTEST_SLIPPAGE_TICKS * config.ORB_TICK
        if direction > 0:
            stop, target = self.or_low, self.or_high + self.or_width
        else:
            stop, target = self.or_high, self.or_low - self.or_width
        self.bracket = self.b.place_bracket(self.sym, config.ORB_MAX_CONTRACTS, direction,
                                            stop, target, tick=config.ORB_TICK)
        # BUG A : in_pos=True SEULEMENT après fill confirmé. Sinon skip le jour (pas de position fantôme).
        if not self.bracket.get("filled"):
            log.warning("ORB entrée NON remplie → skip le jour (in_pos reste False)")
            self.in_pos = False; self.done = True
            return
        # fill d'entrée : RÉEL en live (avgFillPrice), théorique +1 tick adverse en DRY_RUN (BUG 2)
        entry_fill = (px + direction * slip) if config.DRY_RUN else self.bracket["entry_fill"]
        self.in_pos = True
        self.entry_theo = px
        self.entry, self.stop, self.target, self.pos_dir = entry_fill, stop, target, direction
        log.info("ENTRÉE ORB %s théo@%.2f fill@%.2f SL=%.2f TP=%.2f",
                 "LONG" if direction > 0 else "SHORT", px, entry_fill, stop, target)

    def _manage_position(self, now, eod):
        slip = config.BACKTEST_SLIPPAGE_TICKS * config.ORB_TICK
        reason = exit_theo = exit_fill = None

        if not config.DRY_RUN:
            # LIVE : le bracket IB exécute SL/TP côté broker → lire le VRAI fill (BUG 1)
            ex = self.b.bracket_exit_fill(self.bracket)
            if ex is not None:
                exit_fill, reason = ex
                exit_theo = self.target if reason == "TARGET" else self.stop
            elif now.time() >= eod:
                # BUG B : cancel D'ABORD, re-check fill (un leg a pu filler pendant le cancel),
                # puis get_position = source de vérité avant tout ordre de sortie
                self.b.cancel_all(self.sym)
                ex2 = self.b.bracket_exit_fill(self.bracket)
                if ex2 is not None:
                    exit_fill, reason = ex2
                    exit_theo = self.target if reason == "TARGET" else self.stop
                else:
                    qty, _ = self.b.get_position(self.sym)
                    if qty != 0:
                        res = self.b.place_market(self.sym, abs(qty), -1 if qty > 0 else 1)
                        exit_fill = res.get("fill"); exit_theo = self.b.last_price(self.sym) or exit_fill
                        reason = "EOD"
                    else:
                        exit_fill = self.b.last_price(self.sym) or self.entry  # déjà flat
                        exit_theo = exit_fill; reason = "EOD"
            else:
                return  # bracket pas encore touché, toujours en position
        else:
            # DRY_RUN : intrabar high/low de la barre 1-min + 1 tick de slippage (BUG 2)
            bars = self.b.get_bars(self.sym, 1, 3)
            last = float(bars["close"].iloc[-1]) if bars is not None and len(bars) else self.entry
            hi = float(bars["high"].iloc[-1]) if bars is not None and len(bars) else last
            lo = float(bars["low"].iloc[-1]) if bars is not None and len(bars) else last
            if self.pos_dir > 0:
                if lo <= self.stop: reason, exit_theo = "STOP", self.stop
                elif hi >= self.target: reason, exit_theo = "TARGET", self.target
            else:
                if hi >= self.stop: reason, exit_theo = "STOP", self.stop
                elif lo <= self.target: reason, exit_theo = "TARGET", self.target
            if reason is None and now.time() >= eod:
                reason, exit_theo = "EOD", last
                self.b.place_market(self.sym, config.ORB_MAX_CONTRACTS, -self.pos_dir)
                self.b.cancel_all(self.sym)
            if reason is None:
                return
            exit_fill = exit_theo - self.pos_dir * slip   # 1 tick adverse

        # P&L NET calculé UNE fois (BUG 5) : fills (slippage) − commission (BUG 6)
        pnl_pts = (exit_fill - self.entry) * self.pos_dir
        fee = config.MNQ_ROUND_TRIP_FEE
        pnl_usd_net = pnl_pts * config.ORB_POINT_VALUE - fee
        self.rm.record_realized(pnl_usd_net, sleeve="ORB", fee=fee)   # source unique
        if config.DRY_RUN:
            obs = config.BACKTEST_SLIPPAGE_TICKS
        else:
            obs = (abs(self.entry - (self.entry_theo or self.entry))
                   + abs(exit_fill - (exit_theo or exit_fill))) / (2 * config.ORB_TICK)
        logger.log_trade("ORB", self.sym, self.pos_dir, self.entry, exit_fill, reason,
                         pnl_dollars=pnl_usd_net, fees=fee, virtual_equity=self.rm.virtual_equity(),
                         observed_slippage_ticks=round(obs, 2), tick=config.ORB_TICK)
        self.in_pos = False
        self.done = True

    def state(self):
        return {"in_pos": self.in_pos, "done": self.done, "or_built": self.or_built,
                "dir_allowed": self.dir_allowed, "breakout_evaluated": self.breakout_evaluated}
