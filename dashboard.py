"""
Dashboard Flask — localhost:5000 uniquement. Monitoring read-only du bot.
Pages : / (status), /trades, /chart (equity vs backtest), /config.
Métriques : equity virtuelle, P&L jour/sem/mois/total, drawdown vs backtest, n trades + winrate
par sleeve, slippage moyen (flag rouge >2× backtest, addendum #7), corrélation ORB/Overnight 30j.
"""
import datetime as dt
import json
import numpy as np
import pandas as pd
from flask import Flask, jsonify
import config, logger

app = Flask(__name__)
_CTX = {}


def bind(broker, rm, orb, overnight):
    _CTX.update(broker=broker, rm=rm, orb=orb, overnight=overnight)


def _metrics():
    rm = _CTX.get("rm")
    trades = logger.read_all_trades()
    df = pd.DataFrame(trades) if trades else pd.DataFrame()
    out = {"dry_run": config.DRY_RUN, "halted": rm.halted if rm else None,
           "halt_reason": rm.halt_reason if rm else None,
           "virtual_equity": rm.virtual_equity() if rm else config.CAPITAL_BASE,
           "capital_base": config.CAPITAL_BASE}
    if len(df):
        df["ts"] = pd.to_datetime(df["timestamp"])
        now = dt.datetime.now()
        out["pnl_total"] = round(df["pnl_dollars"].sum(), 2)
        out["pnl_today"] = round(df[df["ts"].dt.date == now.date()]["pnl_dollars"].sum(), 2)
        out["pnl_week"] = round(df[df["ts"] >= now - dt.timedelta(days=7)]["pnl_dollars"].sum(), 2)
        out["pnl_month"] = round(df[df["ts"] >= now - dt.timedelta(days=30)]["pnl_dollars"].sum(), 2)
        # equity curve + drawdown
        eq = config.CAPITAL_BASE + df["pnl_dollars"].cumsum()
        dd = (eq - eq.cummax()) / eq.cummax()
        out["current_drawdown_pct"] = round(100 * float(dd.iloc[-1]), 2)
        out["max_drawdown_pct"] = round(100 * float(dd.min()), 2)
        out["backtest_mdd_orb_pct"] = config.ORB_BACKTEST_OOS_MDD
        out["backtest_mdd_on_pct"] = config.ON_BACKTEST_OOS_MDD
        for sl in ("ORB", "OVERNIGHT"):
            sub = df[df["sleeve"] == sl]
            wins = sub[sub["pnl_dollars"] > 0]
            out[f"n_{sl.lower()}"] = len(sub)
            out[f"winrate_{sl.lower()}"] = round(100*len(wins)/len(sub), 1) if len(sub) else None
        # slippage moyen + alertes (addendum #7)
        slips = df["slippage_vs_backtest_ticks"].dropna()
        out["avg_slippage_ticks"] = round(float(slips.mean()), 2) if len(slips) else None
        out["slippage_alerts"] = int(df.get("slippage_alert", pd.Series(dtype=bool)).fillna(False).sum())
        # corrélation ORB vs Overnight P&L (rolling 30j, par jour)
        out["corr_orb_overnight_30d"] = _corr_30d(df)
    return out


def _corr_30d(df):
    try:
        df = df.copy()
        df["d"] = pd.to_datetime(df["timestamp"]).dt.date
        piv = df.pivot_table(index="d", columns="sleeve", values="pnl_dollars", aggfunc="sum").fillna(0)
        if "ORB" in piv and "OVERNIGHT" in piv and len(piv) >= 10:
            tail = piv.tail(30)
            return round(float(tail["ORB"].corr(tail["OVERNIGHT"])), 3)
    except Exception:
        pass
    return None


@app.route("/")
def index():
    m = _metrics()
    orb = _CTX.get("orb"); on = _CTX.get("overnight"); b = _CTX.get("broker")
    pos = {}
    try:
        pos["MNQ"] = b.get_position(config.ORB_SYMBOL)[0]
        pos["MES"] = b.get_position(config.ON_SYMBOL)[0]
        ib_ok = b.is_connected()
    except Exception:
        ib_ok = False
    rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in m.items())
    banner = ""
    if m.get("halted"):
        banner = f"<div style='background:#c00;color:#fff;padding:8px'>🛑 HALTED — {m.get('halt_reason')}</div>"
    if config.DRY_RUN:
        banner += "<div style='background:#e6a23c;color:#fff;padding:8px'>DRY_RUN — aucun ordre réel</div>"
    slip_warn = ""
    if m.get("slippage_alerts", 0):
        slip_warn = f"<div style='background:#c00;color:#fff;padding:8px'>⚠️ {m['slippage_alerts']} trade(s) slippage > 2× backtest</div>"
    html = f"""<html><head><title>Stack2/3 Bot</title><meta http-equiv=refresh content=15>
    <style>body{{font-family:monospace;margin:20px}}table{{border-collapse:collapse}}td{{border:1px solid #ccc;padding:4px 8px}}</style></head>
    <body>{banner}{slip_warn}
    <h2>Stack #2/#3 — Pool $10k</h2>
    <p>IB connecté: <b>{'✅' if ib_ok else '❌'}</b> | Positions: MNQ={pos.get('MNQ','?')} MES={pos.get('MES','?')}</p>
    <table>{rows}</table>
    <p><a href=/trades>trades</a> | <a href=/chart>chart</a> | <a href=/config>config</a> | <a href=/api>json</a></p>
    </body></html>"""
    return html


@app.route("/api")
def api():
    return jsonify(_metrics())


@app.route("/trades")
def trades():
    ts = logger.read_all_trades()[-200:][::-1]
    head = "<tr><th>ts</th><th>sleeve</th><th>dir</th><th>entry</th><th>exit</th><th>reason</th><th>$pnl</th><th>slip</th></tr>"
    rows = ""
    for t in ts:
        bg = "background:#fdd" if t.get("slippage_alert") else ""
        rows += (f"<tr style='{bg}'><td>{t['timestamp']}</td><td>{t['sleeve']}</td><td>{t['direction']}</td>"
                 f"<td>{t['entry_price']}</td><td>{t['exit_price']}</td><td>{t['exit_reason']}</td>"
                 f"<td>{t['pnl_dollars']}</td><td>{t.get('slippage_vs_backtest_ticks')}</td></tr>")
    return f"<html><body style='font-family:monospace'><h3>Trades</h3><table border=1>{head}{rows}</table><p><a href=/>back</a></p></body></html>"


@app.route("/chart")
def chart():
    ts = logger.read_all_trades()
    if not ts:
        return "Pas encore de trade. <a href=/>back</a>"
    df = pd.DataFrame(ts)
    eq = (config.CAPITAL_BASE + df["pnl_dollars"].cumsum()).round(2).tolist()
    return (f"<html><body style='font-family:monospace'><h3>Equity virtuelle (base $10k)</h3>"
            f"<p>{eq}</p><p>Backtest MDD attendu : ORB {config.ORB_BACKTEST_OOS_MDD}% / Overnight {config.ON_BACKTEST_OOS_MDD}%</p>"
            f"<p><a href=/>back</a></p></body></html>")


@app.route("/config")
def show_config():
    keys = [k for k in dir(config) if k.isupper()]
    rows = "".join(f"<tr><td>{k}</td><td>{getattr(config,k)}</td></tr>" for k in keys)
    return f"<html><body style='font-family:monospace'><h3>Config (read-only)</h3><table border=1>{rows}</table><p><a href=/>back</a></p></body></html>"


def start_dashboard():
    app.run(host=config.DASH_HOST, port=config.DASH_PORT, debug=False, use_reloader=False)
