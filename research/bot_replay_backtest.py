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

import logging
logging.disable(logging.WARNING)   # couper le flood WARNING (daily stop, etc.) en replay

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


def _metrics(trades, cap, lo=None, hi=None):
    ts = [t for t in trades if (lo is None or pd.Timestamp(t["timestamp"]) >= lo)
          and (hi is None or pd.Timestamp(t["timestamp"]) < hi)]
    if len(ts) < 5:
        return {}
    ts = sorted(ts, key=lambda x: x["timestamp"])
    idx = pd.to_datetime([t["timestamp"] for t in ts])
    pnl = pd.Series([t["pnl_dollars"] for t in ts], index=idx)
    eq = cap + pnl.cumsum()
    days = max((eq.index[-1] - eq.index[0]).days, 1)
    tot = float(eq.iloc[-1] / cap - 1)
    net_pfu = tot - max(0.0, tot) * 0.30                  # PFU 30% sur gain net positif
    ann_gross = (1 + tot) ** (365 / days) - 1
    ann_pfu = (1 + net_pfu) ** (365 / days) - 1
    dd = (eq - eq.cummax()) / eq.cummax()
    # Sharpe sur equity quotidienne
    eqd = eq.resample("1D").last().ffill()
    r = eqd.pct_change().dropna()
    sh = float(r.mean() / r.std() * (252 ** 0.5)) if r.std() > 0 else 0.0
    return {"net_total_pct": round(100 * tot, 2),
            "net_ann_gross_pct": round(100 * ann_gross, 2),
            "net_ann_pfu_pct": round(100 * ann_pfu, 2),
            "mdd_pct": round(100 * float(dd.min()), 2),
            "sharpe": round(sh, 3),
            "pnl_$": round(float(pnl.sum()), 2), "n_trades": len(ts),
            "final_equity_$": round(float(eq.iloc[-1]), 0)}


def main():
    months = int(os.getenv("REPLAY_MONTHS", "6"))
    cap = float(os.getenv("CAPITAL_BASE_REPLAY", "30000"))
    config.CAPITAL_BASE = cap
    config.KILL_EQUITY = cap * 0.80
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
        d = broker.now.date()
        for off in range(0, 7):
            v = vix_map.get(d - dt.timedelta(days=off))
            if v is not None:
                return v
        return 999.0
    strategy_overnight.get_vix_close = hist_vix

    # ── Grille temporelle : chaque minute en [9:00,16:30] ET + chaque 15 min overnight ──
    grid = nq.index.union(es.index)
    grid = grid[grid >= start]
    broker.now = grid[0]

    # ── INJECTION DE L'HORLOGE SIMULÉE ───────────────────────────────────────
    # Le bot utilise dt.datetime.now() (logger) et dt.date.today() (risk_manager._roll_day,
    # current_day, reload_state) — heure RÉELLE, correcte en live mais fausse en replay.
    # On patche le module `dt` de logger et risk_manager pour qu'ils lisent broker.now.
    # (Le CODE du bot est inchangé ; seule la référence module est swappée dans le harnais.)
    import datetime as _rdt, types as _types
    _shim = _types.SimpleNamespace(
        datetime=_types.SimpleNamespace(now=lambda: broker.now.replace(tzinfo=None),
                                        fromisoformat=_rdt.datetime.fromisoformat),
        date=_types.SimpleNamespace(today=lambda: broker.now.date()),
        timedelta=_rdt.timedelta, time=_rdt.time)
    logger.dt = _shim
    risk_manager.dt = _shim

    rm = risk_manager.RiskManager(broker)
    orb = strategy_orb.ORBStrategy(broker, rm)
    on = strategy_overnight.OvernightStrategy(broker, rm)
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
    IS_END = pd.Timestamp("2024-07-01")
    by_year = {}
    for y in range(2021, 2027):
        ty = [t for t in trades if pd.Timestamp(t["timestamp"]).year == y]
        if len(ty) >= 3:
            by_year[str(y)] = round(sum(t["pnl_dollars"] for t in ty), 2)
    out = {
        "window": [str(start.date()), str(end.date())], "months": months,
        "capital_base_$": cap, "kill_equity_$": cap * 0.80,
        "run_cycle_calls": n_calls,
        "halted": rm.halted, "halt_reason": rm.halt_reason,
        "realized_pnl_$": round(rm.realized_pnl, 2),
        "fees_paid_$": round(rm.fees_paid, 2),
        "ORB_sleeve": stats(orb_tr), "OVERNIGHT_sleeve": stats(on_tr),
        "COMBO_full": _metrics(trades, cap),
        "COMBO_IS_2021_2024H1": _metrics(trades, cap, hi=IS_END),
        "COMBO_OOS_2024H2_2026": _metrics(trades, cap, lo=IS_END),
        "pnl_by_year_$": by_year,
        "n_trades_total": len(trades),
    }
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    if os.getenv("RUN_REPLAY") != "1":
        raise SystemExit("RUN_REPLAY=1 requis.")
    main()
