#!/usr/bin/env python3
"""
Replay backtest du CODE EXACT du bot (strategy_orb + strategy_overnight + risk_manager
avec tous les fixes d'audit), en DRY_RUN, sur données historiques Dukascopy.

Principe : un FakeBroker sert les barres minute par minute, on avance l'horloge et on
appelle les VRAIS run_cycle(). Aucune ré-implémentation — c'est le code déployable.

Honnêteté :
- Le code live n'est pas vectorisé → step minute par minute. Fenêtre bornée (REPLAY_MONTHS).
- VIX historique injecté (monkeypatch get_vix_close) par date — la LOGIQUE (VIX<25) reste
  celle du bot, seule la source data est rendue historique.
- Data = Dukascopy index (proxy ES/NQ, validé 0.998 vs CME) ; MNQ↔NQ, MES↔ES.
- DRY_RUN : slippage 1 tick/jambe + frais $1.50/$1.12 RT appliqués par le code du bot.

RUN_REPLAY=1 python3 bot_replay_backtest.py    (REPLAY_MONTHS=6 par défaut)
"""
import os, sys, json, tempfile, datetime as dt
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # stack2-orb/ pour importer les modules du bot
DATA = os.path.expanduser("~/.openclaw/workspace/jim-bot/trading-agent/research/data_cache/cme")
ET = "America/New_York"

import config
config.DRY_RUN = True
config.LOG_DIR = tempfile.mkdtemp(prefix="replay_logs_")
config.DAILY_SUMMARY_FILE = os.path.join(config.LOG_DIR, "ds.json")
config.HEARTBEAT_FILE = os.path.join(config.LOG_DIR, "hb.log")

import logger, importlib
importlib.reload(logger)
import calendar_util as cal
import strategy_overnight, strategy_orb, risk_manager


# ── Données ───────────────────────────────────────────────────────────────────
def load_1m(name):
    df = pd.read_parquet(os.path.join(DATA, f"{name}_1m.parquet")).tz_convert(ET)
    return df[["open", "high", "low", "close"]].sort_index()

def resample(df, rule):
    o = df.resample(rule, closed="left", label="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    return o

def daily_rth(df):
    t = df.index.time
    rth = df[(t >= dt.time(9, 30)) & (t < dt.time(16, 0))]
    d = rth.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    return d


class FakeBroker:
    """Sert les barres historiques jusqu'à self.now. Ordres = no-op (DRY_RUN)."""
    def __init__(self, m1, m5, dly):
        self.m1, self.m5, self.dly = m1, m5, dly
        self.now = None
        self.account = "SIM"

    def _slice(self, df, n, tf_min):
        # ANTI LOOK-AHEAD : une barre étiquetée T (closed='left') couvre [T, T+tf) et n'est
        # COMPLÈTE qu'à T+tf. À self.now, ne servir que les barres avec T+tf <= now,
        # i.e. label T <= now - tf. Sinon on verrait la barre en formation (future).
        cutoff = self.now - pd.Timedelta(minutes=int(tf_min))
        pos = df.index.searchsorted(cutoff, side="right")
        sub = df.iloc[max(0, pos - n):pos]
        if len(sub) == 0:
            return None
        out = sub.reset_index()
        out.columns = ["date"] + list(out.columns[1:])
        return out

    def get_bars(self, sym, tf_min, n):
        df = self.m1[sym] if int(tf_min) == 1 else self.m5[sym]
        return self._slice(df, n, int(tf_min))

    def get_daily_closes(self, sym, n):
        d = self.dly[sym]
        d = d[d.index.normalize() < pd.Timestamp(self.now.date(), tz=ET)]   # jusqu'à HIER (confirmés)
        sub = d.iloc[-n:]
        if len(sub) == 0:
            return None
        out = sub.reset_index()
        out.columns = ["date"] + list(out.columns[1:])
        return out

    def last_price(self, sym):
        # dernier 1-min COMPLET (= comportement live sans souscription streaming, fallback barres)
        df = self.m1[sym]
        cutoff = self.now - pd.Timedelta(minutes=1)
        pos = df.index.searchsorted(cutoff, side="right")
        return float(df["close"].iloc[pos - 1]) if pos > 0 else None

    def place_bracket(self, sym, qty, direction, stop, target, tick=0.25):
        return {"dry_run": True, "filled": True, "entry_fill": self.last_price(sym),
                "tp_trade": None, "sl_trade": None}
    def place_market(self, sym, qty, direction):
        return {"dry_run": True, "fill": self.last_price(sym)}
    def cancel_all(self, sym): pass
    def get_position(self, sym): return 0, 0.0
    def get_ib_realized_pnl(self): return None


def main():
    months = int(os.getenv("REPLAY_MONTHS", "6"))
    nq = load_1m("NQ"); es = load_1m("ES")
    end = nq.index[-1]
    start = end - pd.DateOffset(months=months)
    nq = nq[nq.index >= start]; es = es[es.index >= start]

    m1 = {"MNQ": nq, "MES": es}
    m5 = {"MNQ": resample(nq, "5min"), "MES": resample(es, "5min")}
    dly = {"MNQ": daily_rth(load_1m("NQ")), "MES": daily_rth(load_1m("ES"))}  # daily complet (pour EMA/ATR warmup)

    # VIX historique (monkeypatch — logique du bot inchangée, source rendue historique)
    import yfinance as yf
    vx = yf.download("^VIX", start=str((start - pd.DateOffset(months=2)).date()),
                     end=str((end + pd.DateOffset(days=2)).date()), interval="1d",
                     progress=False, auto_adjust=False)
    if hasattr(vx.columns, "get_level_values"):
        vx.columns = vx.columns.get_level_values(0)
    vix_map = {d.date(): float(c) for d, c in vx["Close"].dropna().items()}
    broker = FakeBroker(m1, m5, dly)
    def hist_vix(*_):   # get_vix_close prend broker en arg ; on l'ignore (VIX injecté par date)
        # dernier VIX connu <= date courante
        d = broker.now.date()
        for off in range(0, 7):
            v = vix_map.get(d - dt.timedelta(days=off))
            if v is not None:
                return v
        return 999.0
    strategy_overnight.get_vix_close = hist_vix

    rm = risk_manager.RiskManager(broker)
    orb = strategy_orb.ORBStrategy(broker, rm)
    on = strategy_overnight.OvernightStrategy(broker, rm)

    # ── Grille temporelle : chaque minute en [9:00,16:30] ET + chaque 15 min overnight ──
    grid = nq.index.union(es.index)
    grid = grid[grid >= start]
    n_calls = 0
    for t in grid:
        tt = t.time()
        in_session = dt.time(9, 0) <= tt <= dt.time(16, 30)
        if not (in_session or t.minute % 15 == 0):
            continue
        broker.now = t
        try:
            orb.run_cycle(t)
            on.run_cycle(t)
        except Exception as e:
            print(f"WARN run_cycle {t}: {type(e).__name__}: {e}")
        n_calls += 1

    # ── Résultats (depuis le code du bot) ─────────────────────────────────────
    trades = logger.read_all_trades()
    orb_tr = [x for x in trades if x["sleeve"] == "ORB"]
    on_tr = [x for x in trades if x["sleeve"] == "OVERNIGHT"]
    def stats(ts):
        if not ts: return {}
        pnls = [x["pnl_dollars"] for x in ts]
        wins = [p for p in pnls if p > 0]
        return {"n": len(ts), "pnl_$": round(sum(pnls), 2),
                "win_rate": round(100*len(wins)/len(ts), 1),
                "fees_$": round(sum(x.get("fees", 0) for x in ts), 2),
                "avg_slip_ticks": round(np.mean([x.get("slippage_vs_backtest_ticks") or 0 for x in ts]), 2)}
    out = {
        "window": [str(start.date()), str(end.date())], "months": months,
        "run_cycle_calls": n_calls,
        "final_virtual_equity": round(rm.virtual_equity(), 2),
        "realized_pnl_$": round(rm.realized_pnl, 2),
        "fees_paid_$": round(rm.fees_paid, 2),
        "ORB": stats(orb_tr), "OVERNIGHT": stats(on_tr),
        "n_trades_total": len(trades),
    }
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    if os.getenv("RUN_REPLAY") != "1":
        raise SystemExit("RUN_REPLAY=1 requis.")
    main()
