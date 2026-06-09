# IB Gateway — Setup Mac (Stack #2 paper trading)

Guide pas-à-pas pour Hippo. Objectif : IB Gateway en **paper trading** qui écoute l'API sur **port 4002**, accessible par Zeus via `ib_insync`. ~15-20 min.

---

## Prérequis
- Compte **IB Paper Trading** (déjà créé). Identifiant compte = `DUxxxxxx`.
- macOS.

---

## Étape 1 — Télécharger IB Gateway (PAS TWS)

IB Gateway est plus léger que TWS et fait pour le headless/API.

- URL : <https://www.interactivebrokers.com/en/trading/ibgateway-stable.php>
- Choisir **macOS** → télécharger le **.dmg** "IB Gateway — Stable".
- Installer (glisser dans Applications).

> Alternative robuste si tu veux l'auto-restart : **IBC** (IB Controller) automatise le login Gateway. Pas requis pour le MVP — on commence en lancement manuel.

---

## Étape 2 — Lancer IB Gateway en mode Paper

1. Ouvrir **IB Gateway** (Applications).
2. À l'écran de login : choisir **IB API** (pas FIX).
3. **Trading Mode : Paper Trading** (toggle en bas, IMPORTANT — pas Live).
4. Username / Password de ton compte **paper** (le login se fait DANS Gateway, pas dans le code).
5. Se connecter. Gateway reste ouvert (petite fenêtre de statut).

---

## Étape 3 — Configurer l'API (port 4002, autoriser 127.0.0.1)

Dans IB Gateway → menu **Configure** (ou Configuration) → **Settings** → **API → Settings** :

- ☑️ **Enable ActiveX and Socket Clients**
- **Socket port** : **`4002`** (c'est le port paper par défaut de Gateway ; le live serait 4001)
- ☑️ **Allow connections from localhost only** (sécurité — on ne sort pas de la machine)
- **Trusted IPs** : ajouter `127.0.0.1` si demandé
- ☐ **Read-Only API** : **DÉCOCHER** (on a besoin de passer des ordres en Phase D ; pour Phase B/C lecture seule ça pourrait rester coché, mais décoche dès maintenant pour éviter de refaire)
- **Master API client ID** : laisser vide
- Appliquer / OK.

> ⚠️ Master/Settings → **Auto restart** : IB Gateway force un logout quotidien (~vers minuit) pour maintenance. Configurer "Auto restart" (pas "Auto logoff") dans **Configure → Lock and Exit** pour qu'il se reconnecte seul. Sinon le bot perdra la connexion chaque nuit.

---

## Étape 4 — Renseigner les credentials pour Zeus

Créer le fichier **`~/.openclaw/secrets/ib_demo.json`** (un template existe : `ib_demo.json.template`) :

```json
{
  "username": "ton_user_paper",
  "password": "ton_password_paper",
  "port": 4002,
  "host": "127.0.0.1",
  "account": "DUxxxxxx"
}
```

> `username`/`password` ne sont **pas** utilisés par le code (le login se fait dans Gateway). On les garde pour référence. Seuls `host`/`port`/`account` servent à l'API. Le fichier est dans `~/.openclaw/secrets/` (hors repo, gitignored).

```bash
chmod 600 ~/.openclaw/secrets/ib_demo.json
```

---

## Étape 5 — Test de connexion (Zeus lance)

Une fois Gateway lancé + loggé + API configurée + `ib_demo.json` rempli, Zeus exécute :

```bash
cd ~/.openclaw/workspace/stack2-orb/research
../venv/bin/python ib_connect_test.py
```

Attendu : `✅ CONNEXION STABLE — Gateway ↔ ib_insync ↔ Zeus OK`, liste des contrats NQ, et 60 barres 1-min de snapshot. Log : `research/ib_connection_test.log`.

---

## Dépannage

| Symptôme | Cause probable | Fix |
|---|---|---|
| `ConnectionRefusedError` port 4002 | Gateway pas lancé / pas loggé | relancer Gateway, se logger paper |
| Connexion OK mais `0 barres` | pas de souscription market data NQ | IB fournit le **historique gratuit** aux clients ; vérifier que le compte paper est rattaché à un live funded (sinon data limitée). Pour l'historique 1-min CME, souscription "CME data" peut être requise (~$/mois) ou data différée. |
| `clientId already in use` | une autre session API tourne | changer clientId ou fermer l'autre |
| Logout nocturne | auto-logoff Gateway | activer **Auto restart** (Étape 3) |

> **Point d'attention data** : l'historique 1-min CME via IB peut nécessiter une **souscription market data CME** (~$11/mois pour CME Real-Time, ou data différée gratuite). À vérifier en Phase C — si l'historique 5 ans 1-min n'est pas accessible gratuitement sur le compte paper, on basculera sur Databento (essai gratuit ~$125 crédit) ou on validera la fidélité déjà mesurée (Dukascopy 0.998 vs NQ=F).

---

_Une fois ces 5 étapes faites, ping Zeus : il lance `ib_connect_test.py` et valide la Phase B._
