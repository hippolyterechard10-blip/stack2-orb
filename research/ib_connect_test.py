#!/usr/bin/env python3
"""
Stack #2 Phase B — Test de connexion IB Gateway via ib_insync.
Lit les credentials dans ~/.openclaw/secrets/ib_demo.json :
  {"username","password","port":4002,"host":"127.0.0.1","account":"DUxxxxxx"}
(username/password ne sont PAS utilisés par ib_insync — le login se fait dans IB Gateway
lui-même ; on n'utilise ici que host/port/account pour la connexion API.)

Vérifie : connexion OK, liste contrats NQ (front + back), snapshot 1-min 60 dernières minutes.
Sortie : research/ib_connection_test.log. Aucun ordre, aucun trade. READ-ONLY.
Lancer : ../venv/bin/python ib_connect_test.py
"""
import os, json, sys, logging, datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(ROOT, "ib_connection_test.log")
SECRETS = os.path.expanduser("~/.openclaw/secrets/ib_demo.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.FileHandler(LOG), logging.StreamHandler(sys.stdout)])
log = logging.getLogger("ib_test")


def load_secrets():
    if not os.path.exists(SECRETS):
        log.error("Credentials absents : %s — Hippo doit créer ce fichier (cf. IB_SETUP.md).", SECRETS)
        sys.exit(2)
    with open(SECRETS) as f:
        s = json.load(f)
    for k in ("host", "port", "account"):
        if k not in s:
            log.error("Clé manquante dans ib_demo.json : %s", k); sys.exit(2)
    return s


def main():
    s = load_secrets()
    host, port, account = s["host"], int(s["port"]), s["account"]
    log.info("=== Test connexion IB Gateway ===")
    log.info("host=%s port=%s account=%s", host, port, account)

    try:
        from ib_insync import IB, Future, ContFuture
    except Exception as e:
        log.error("ib_insync non installé : %s", e); sys.exit(2)

    ib = IB()
    try:
        ib.connect(host, port, clientId=11, timeout=15)
    except Exception as e:
        log.error("ÉCHEC connexion (%s). Gateway lancé ? Port %s paper ? API activée ? "
                  "127.0.0.1 autorisé ?", e, port)
        log.error("→ voir IB_SETUP.md. Verdict Phase B : BLOQUÉ (connexion).")
        sys.exit(1)

    log.info("✅ Connecté. serverVersion=%s", ib.client.serverVersion())
    try:
        accts = ib.managedAccounts()
        log.info("Comptes gérés : %s", accts)
        if account not in accts:
            log.warning("Compte %s pas dans les comptes gérés %s (vérifier ib_demo.json)", account, accts)

        # ── Contrats NQ (front + back months) ──────────────────────────────
        det = ib.reqContractDetails(Future("NQ", exchange="CME"))
        log.info("Contrats NQ trouvés : %d", len(det))
        rows = sorted([(d.contract.lastTradeDateOrContractMonth, d.contract.localSymbol,
                        d.contract.conId) for d in det])
        for exp, ls, cid in rows[:8]:
            log.info("  NQ %s  local=%s  conId=%s", exp, ls, cid)

        # ── Continuous future + snapshot 1-min 60 min ──────────────────────
        cont = ContFuture("NQ", exchange="CME")
        ib.qualifyContracts(cont)
        log.info("ContFuture NQ qualifié : conId=%s", cont.conId)
        bars = ib.reqHistoricalData(cont, endDateTime="", durationStr="3600 S",
                                    barSizeSetting="1 min", whatToShow="TRADES",
                                    useRTH=False, formatDate=1)
        log.info("Snapshot 1-min : %d barres reçues", len(bars))
        if bars:
            b0, b1 = bars[0], bars[-1]
            log.info("  première : %s O=%s H=%s L=%s C=%s", b0.date, b0.open, b0.high, b0.low, b0.close)
            log.info("  dernière : %s O=%s H=%s L=%s C=%s", b1.date, b1.open, b1.high, b1.low, b1.close)
            log.info("✅ Data feed fonctionnel.")
            log.info("VERDICT PHASE B : ✅ CONNEXION STABLE — Gateway ↔ ib_insync ↔ Zeus OK.")
        else:
            log.warning("0 barre reçue — feed data NQ peut nécessiter une souscription market data IB.")
            log.info("VERDICT PHASE B : ⚠️ Connexion OK mais data NQ vide (vérifier subscriptions).")
    finally:
        ib.disconnect()
        log.info("Déconnecté proprement.")


if __name__ == "__main__":
    main()
