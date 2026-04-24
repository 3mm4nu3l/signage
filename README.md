# Binance Green Pairs Scanner (Simulation)

Script Python pour:
- récupérer les paires Binance Futures **dans le vert** (`priceChangePercent > 0`)
- filtrer avec volume 24h minimum (`quoteVolume >= 5M` par défaut)
- appliquer le signal inspiré du Pine Script (volume spike + breakout + bougie haussière + cooldown)
- simuler un BUY et envoyer une alerte Telegram
- évaluer le signal sur **bougie clôturée** pour éviter les signaux intrabar qui disparaissent

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Variables Telegram (optionnel)

```bash
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
```

Si non configuré, le script logge simplement l'alerte en console.

## Lancement

Timeframe par défaut: `15m`.

```bash
python bot_simulator.py
```

Exemple avec paramètres:

```bash
python bot_simulator.py --timeframe 15m --min-quote-volume 5000000 --poll-seconds 30
```

Avec décalage horaire d'affichage (défaut UTC-2):

```bash
python bot_simulator.py --tz-offset-hours -2
```

## Notes

- Aucune exécution réelle d'ordre: **simulation seulement**.
- Source marché: Binance Futures API publique.
