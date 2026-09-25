# Deriv Insight v1.0 — Digit Probability Research

Deriv Insight is a separate, risk-first research platform for Deriv synthetic markets. It is **not** a Martingale bot and v1 contains a deliberate hard lock against real-money execution.

## What v1 does

- Discovers currently active synthetic symbols from Deriv's public WebSocket.
- Downloads up to 5,000 recent ticks per on-demand study.
- Extracts last digits using the API pip size.
- Calculates digit frequencies, entropy, chi-square diagnostic, repeat rate and a 10×10 transition matrix.
- Evaluates Digit Match/Differs, Over/Under and Even/Odd.
- Fetches a live `proposal`, reads `ask_price` and `payout`, and calculates break-even probability as `ask_price / payout`.
- Estimates empirical probability with a deliberately shrunk one-step transition component.
- Calculates Wilson 95% confidence bounds and labels `NO TRADE`, `RESEARCH`, or `DEMO CANDIDATE`.
- Runs no-look-ahead tick backtests using an assumed historical profit-return percentage.
- Supports manual **demo-only** contract execution after both account-level and fresh contract-level gates pass.
- Provides a collector command that builds long-run digit and transition aggregates without storing every tick.

## Important research distinction

The 792.9% Match figure is treated as a *current/assumed return*, not a permanent property of Digit Match. Live decisions use Deriv's fresh proposal. Historical backtests must state the assumed return because historical proposal prices are not present in tick history.

## Pages

- `/` Overview
- `/digits/` Digit Laboratory
- `/radar/` Probability Radar
- `/backtests/` Walk-forward tick backtests
- `/demo/` Demo Trading Gate
- `/settings/` Risk Settings
- `/admin/` Django admin

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## Demo connection

Public research needs no Deriv credentials. When you later decide to test the demo account, configure these privately in Render (never put them in GitHub or chat):

- `DERIV_AUTH_TOKEN`
- `DERIV_APP_ID` when PAT authentication requires it
- `DERIV_ACCOUNT_ID` (demo Options account)
- `DERIV_DEMO_ENABLED=true`

Then also enable **Demo execution** in the application's Risk Settings. Both switches are required.

The server requests a short-lived OTP from Deriv and refuses a returned WebSocket URL containing `/real?`.

## Real trading

`settings.DERIV_REAL_ENABLED = False` is hard-coded. v1 does not implement a real trading path.

## Long-run evidence collection

Run as a separate process/Render background worker only when you want persistent evidence:

```bash
python manage.py collect_digits --symbols 1HZ100V --flush 250
```

Add other symbol codes only after confirming them with Active Symbols / Contracts For. The collector aggregates counts and transitions rather than writing every tick into Postgres.
