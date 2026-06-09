"""
Couche broker Interactive Brokers via ib_insync. Paper port 4002, account DUQ700122.
Supporte DRY_RUN (log sans ordre réel). Reconnexion auto. Contrats front-month MNQ/MES.

⚠️ Historique : data continue via ContFuture ; ORDRES via le contrat front-month réel
(on ne trade pas un continu). Le front est résolu + caché, re-résolu si proche expiry.
"""
import os, json, time, datetime as dt
import logging
from ib_insync import IB, Future, ContFuture, MarketOrder, LimitOrder, StopOrder, util

import config

log = logging.getLogger("broker")


class IBBroker:
    def __init__(self):
        self.ib = IB()
        self._front = {}        # symbol -> Future (front-month tradeable)
        self._cont = {}         # symbol -> ContFuture (historique continu)
        self._last_connect_ok = None

    # ── Connexion ────────────────────────────────────────────────────────────
    def _secrets(self):
        p = os.path.expanduser(config.IB_SECRETS)
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f)
        return {}

    def connect(self):
        s = self._secrets()
        host = s.get("host", config.IB_HOST)
        port = int(s.get("port", config.IB_PORT))
        self.ib.connect(host, port, clientId=config.IB_CLIENT_ID, timeout=15)
        self._last_connect_ok = time.time()
        log.info("✅ IB connecté %s:%s account=%s serverVersion=%s",
                 host, port, config.IB_ACCOUNT, self.ib.client.serverVersion())
        # qualifier les contrats
        for sym in (config.ORB_SYMBOL, config.ON_SYMBOL):
            self._resolve_contracts(sym)

    def is_connected(self):
        return self.ib.isConnected()

    def ensure_connected(self) -> bool:
        """Retry toutes les 30s jusqu'à IB_RECONNECT_TIMEOUT_SEC, sinon False (halt)."""
        if self.is_connected():
            return True
        start = time.time()
        while time.time() - start < config.IB_RECONNECT_TIMEOUT_SEC:
            try:
                log.warning("Connexion IB perdue — tentative reconnexion…")
                self.connect()
                if self.is_connected():
                    log.info("✅ Reconnecté.")
                    return True
            except Exception as e:
                log.warning("Reco échouée : %s", e)
            self.ib.sleep(config.IB_RECONNECT_RETRY_SEC)
        log.error("❌ Pas reconnecté après %ss — HALT trading.", config.IB_RECONNECT_TIMEOUT_SEC)
        return False

    # ── Contrats ─────────────────────────────────────────────────────────────
    def _resolve_contracts(self, symbol):
        cont = ContFuture(symbol, exchange="CME")
        self.ib.qualifyContracts(cont)
        self._cont[symbol] = cont
        det = self.ib.reqContractDetails(Future(symbol, exchange="CME"))
        today = dt.date.today().strftime("%Y%m%d")
        live = sorted([d.contract for d in det
                       if d.contract.lastTradeDateOrContractMonth >= today],
                      key=lambda c: c.lastTradeDateOrContractMonth)
        if live:
            self._front[symbol] = live[0]
            log.info("%s front-month : %s (exp %s)", symbol,
                     live[0].localSymbol, live[0].lastTradeDateOrContractMonth)
        return self._front.get(symbol)

    def front(self, symbol):
        if symbol not in self._front:
            self._resolve_contracts(symbol)
        return self._front[symbol]

    # ── Data ─────────────────────────────────────────────────────────────────
    def get_bars(self, symbol, tf_min, n):
        """n dernières barres de tf_min minutes (intraday, RTH inclus). DataFrame."""
        dur = max(int(n * tf_min * 60 * 1.5), 60)
        bars = self.ib.reqHistoricalData(
            self._cont.get(symbol) or self.front(symbol), endDateTime="",
            durationStr=f"{dur} S", barSizeSetting=f"{tf_min} mins",
            whatToShow="TRADES", useRTH=False, formatDate=1)
        df = util.df(bars)
        return df.tail(n) if df is not None else None

    def get_daily_closes(self, symbol, n):
        """n derniers closes DAILY confirmés (le jour courant non clôturé n'apparaît pas)."""
        bars = self.ib.reqHistoricalData(
            self._cont.get(symbol) or self.front(symbol), endDateTime="",
            durationStr=f"{max(n+10, 30)} D", barSizeSetting="1 day",
            whatToShow="TRADES", useRTH=True, formatDate=1)
        df = util.df(bars)
        return df.tail(n) if df is not None else None

    def last_price(self, symbol):
        df = self.get_bars(symbol, 1, 2)
        return float(df["close"].iloc[-1]) if df is not None and len(df) else None

    # ── Ordres (DRY_RUN-aware) ──────────────────────────────────────────────
    def place_market(self, symbol, qty, direction):
        action = "BUY" if direction > 0 else "SELL"
        px = self.last_price(symbol)
        if config.DRY_RUN:
            log.info("[DRY_RUN] MARKET %s %s x%s @~%s", action, symbol, qty, px)
            return {"dry_run": True, "fill": px, "action": action}
        order = MarketOrder(action, qty)
        trade = self.ib.placeOrder(self.front(symbol), order)
        self.ib.sleep(1)
        fill = trade.orderStatus.avgFillPrice or px
        log.info("MARKET %s %s x%s filled @%s", action, symbol, qty, fill)
        return {"dry_run": False, "fill": fill, "action": action, "trade": trade}

    def place_bracket(self, symbol, qty, direction, stop, target):
        """Entrée market + SL + TP (OCA). DRY_RUN log seulement."""
        action = "BUY" if direction > 0 else "SELL"
        px = self.last_price(symbol)
        if config.DRY_RUN:
            log.info("[DRY_RUN] BRACKET %s %s x%s entry@~%s SL=%s TP=%s",
                     action, symbol, qty, px, stop, target)
            return {"dry_run": True, "fill": px}
        bracket = self.ib.bracketOrder(action, qty, limitPrice=px,
                                       takeProfitPrice=target, stopLossPrice=stop)
        for o in bracket:
            self.ib.placeOrder(self.front(symbol), o)
        self.ib.sleep(1)
        log.info("BRACKET %s %s x%s SL=%s TP=%s placé", action, symbol, qty, stop, target)
        return {"dry_run": False, "fill": px, "bracket": bracket}

    def cancel_all(self, symbol):
        if config.DRY_RUN:
            log.info("[DRY_RUN] cancel_all %s", symbol); return
        for o in self.ib.openOrders():
            if getattr(o.contract, "symbol", None) == symbol:
                self.ib.cancelOrder(o)
        log.info("cancel_all %s", symbol)

    def get_position(self, symbol):
        """Retourne (qty signé, avgCost) ou (0, 0) si flat."""
        for p in self.ib.positions(account=config.IB_ACCOUNT):
            if p.contract.symbol == symbol:
                return int(p.position), float(p.avgCost)
        return 0, 0.0

    def get_equity(self):
        """Equity TOTALE du compte paper IB (NetLiquidation). Le risk_manager la convertit
        en equity virtuelle base $10k via le P&L réalisé."""
        for v in self.ib.accountValues(account=config.IB_ACCOUNT):
            if v.tag == "NetLiquidation" and v.currency == "USD":
                return float(v.value)
        return None

    def disconnect(self):
        try:
            self.ib.disconnect()
        except Exception:
            pass
