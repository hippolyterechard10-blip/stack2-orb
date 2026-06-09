"""
Calendrier de marché US (addendum #3). Source : pandas_market_calendars, calendrier NYSE
(= mêmes jours fériés/demi-séances que CME equity index RTH ; NQ/ES suivent l'horaire cash).

Gère : jours fériés (aucune strat ne trade), demi-séances (close 13:00 ET →
ORB EOD exit 12:55, Overnight entry 12:55).
"""
import datetime as dt
import pandas as pd
import pandas_market_calendars as mcal
import config

_CAL = mcal.get_calendar(config.MARKET_CALENDAR)
_SCHED_CACHE = {}


def _schedule(year):
    if year not in _SCHED_CACHE:
        _SCHED_CACHE[year] = _CAL.schedule(start_date=f"{year}-01-01",
                                           end_date=f"{year}-12-31")
    return _SCHED_CACHE[year]


def is_trading_day(date) -> bool:
    """date = datetime.date (ET). True si séance US ouverte."""
    d = pd.Timestamp(date).normalize()
    sched = _schedule(d.year)
    return d in sched.index


def session_close_et(date) -> dt.time:
    """Heure de close de la séance (16:00 normal, 13:00 demi-séance)."""
    d = pd.Timestamp(date).normalize()
    sched = _schedule(d.year)
    if d not in sched.index:
        return dt.time(16, 0)
    close_utc = sched.loc[d, "market_close"]
    close_et = close_utc.tz_convert(config.TIMEZONE)
    return close_et.time()


def is_half_day(date) -> bool:
    """True si demi-séance (close avant 16:00 ET)."""
    return session_close_et(date) < dt.time(15, 0)


def orb_eod_exit_et(date) -> dt.time:
    """Exit forcé ORB : 15:55 séance normale, 12:55 demi-séance (5 min avant close)."""
    return dt.time(12, 55) if is_half_day(date) else dt.time(15, 55)


def overnight_entry_et(date) -> dt.time:
    """Entry Overnight : 15:55 séance normale, 12:55 demi-séance."""
    return dt.time(12, 55) if is_half_day(date) else dt.time(15, 55)


def session_open_et(date) -> dt.time:
    """Open de séance (9:30 normal — l'open n'est pas avancé en demi-séance)."""
    d = pd.Timestamp(date).normalize()
    sched = _schedule(d.year)
    if d not in sched.index:
        return dt.time(9, 30)
    return sched.loc[d, "market_open"].tz_convert(config.TIMEZONE).time()


def next_trading_day(date):
    """Prochain jour de marché strictement après `date`."""
    d = pd.Timestamp(date).normalize()
    for i in range(1, 8):
        cand = d + pd.Timedelta(days=i)
        if cand.year not in _SCHED_CACHE:
            _schedule(cand.year)
        if cand in _schedule(cand.year).index:
            return cand.date()
    return None


def prev_trading_day(date):
    d = pd.Timestamp(date).normalize()
    for i in range(1, 8):
        cand = d - pd.Timedelta(days=i)
        if cand in _schedule(cand.year).index:
            return cand.date()
    return None
