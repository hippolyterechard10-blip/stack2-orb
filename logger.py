"""
Logging trades (JSONL/jour), résumé quotidien, heartbeat (addendum #8).
P&L virtuel toujours sur base $10k (jamais le solde IB Demo $1M).
"""
import os, json, datetime as dt
import logging
import config

os.makedirs(config.LOG_DIR, exist_ok=True)
_std = logging.getLogger("trades")


def _today_file():
    return os.path.join(config.LOG_DIR, f"{dt.date.today().isoformat()}.jsonl")


def log_trade(sleeve, symbol, direction, entry_price, exit_price, exit_reason,
              pnl_dollars=None, fees=0.0, virtual_equity=0.0, observed_slippage_ticks=None,
              backtest_slippage_ticks=None, tick=0.25, point_value=None):
    """pnl_dollars = P&L NET (calculé UNE fois dans la stratégie, BUG 5). fees = commission RT.
    On NE recalcule PAS le $ ici. pnl_points = points bruts (informatif).
    Fallback : si pnl_dollars non passé → warning + recalcul brut depuis point_value (BUG 5)."""
    if backtest_slippage_ticks is None:
        backtest_slippage_ticks = config.BACKTEST_SLIPPAGE_TICKS
    pnl_points = round((exit_price - entry_price) * (1 if direction > 0 else -1), 2)
    if pnl_dollars is None:
        _std.warning("⚠️ log_trade: pnl_dollars non passé (BUG 5) — recalcul brut de secours")
        pnl_dollars = pnl_points * (point_value or 0.0) - (fees or 0.0)
    slip_alert = (observed_slippage_ticks is not None
                  and observed_slippage_ticks > config.SLIPPAGE_ALERT_MULT * backtest_slippage_ticks)
    rec = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "sleeve": sleeve, "symbol": symbol,
        "direction": "LONG" if direction > 0 else "SHORT",
        "entry_price": round(entry_price, 2), "exit_price": round(exit_price, 2),
        "exit_reason": exit_reason,
        "pnl_points_gross": pnl_points,
        "pnl_dollars": round(pnl_dollars, 2),     # NET (passé par la stratégie, source unique)
        "fees": round(fees, 2),
        "slippage_vs_backtest_ticks": observed_slippage_ticks,
        "slippage_alert": bool(slip_alert),
        "virtual_equity": round(virtual_equity, 2),
        "dry_run": config.DRY_RUN,
    }
    with open(_today_file(), "a") as f:
        f.write(json.dumps(rec) + "\n")
    _std.info("TRADE %s %s %s entry=%s exit=%s reason=%s pnl_net=$%.2f fee=$%.2f eq=$%.2f%s",
              sleeve, rec["direction"], symbol, entry_price, exit_price, exit_reason,
              pnl_dollars, fees, virtual_equity, "  ⚠️SLIPPAGE" if slip_alert else "")
    return rec


def write_daily_summary(summary: dict):
    summary["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
    with open(config.DAILY_SUMMARY_FILE, "w") as f:
        json.dump(summary, f, indent=2)


def heartbeat(equity, positions: dict, status="ALIVE"):
    line = (f"{status} {dt.datetime.now().isoformat(timespec='seconds')} "
            f"equity=${equity:.2f} positions={json.dumps(positions)}\n")
    with open(config.HEARTBEAT_FILE, "a") as f:
        f.write(line)


def read_today_trades():
    """Liste des trades du jour (pour dashboard / daily P&L)."""
    fp = _today_file()
    if not os.path.exists(fp):
        return []
    out = []
    with open(fp) as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                out.append(json.loads(ln))
    return out


def read_all_trades():
    out = []
    for fn in sorted(os.listdir(config.LOG_DIR)):
        if fn.endswith(".jsonl"):
            with open(os.path.join(config.LOG_DIR, fn)) as f:
                for ln in f:
                    ln = ln.strip()
                    if ln:
                        try:
                            out.append(json.loads(ln))
                        except Exception:
                            pass
    return out
