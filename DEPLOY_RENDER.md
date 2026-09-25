# Deploy Deriv Insight v1 to GitHub + Render

## 1. Create a new GitHub repository
Suggested name: `deriv-insight`

Upload the **contents** of this folder to the repository root. Do not put the folder itself inside another folder.

## 2. Deploy with Render Blueprint
The repository includes `render.yaml`.

Create a new Blueprint in Render and select the repository. Render will create:
- `deriv-insight-web`
- `deriv-insight-db`

## 3. Set these environment variables
Render can generate `DJANGO_SECRET_KEY`. Add:

- `ALLOWED_HOSTS=.onrender.com`
- `CSRF_TRUSTED_ORIGINS=https://YOUR-SERVICE.onrender.com`
- `ADMIN_USERNAME=admin` (or your preferred username)
- `ADMIN_PASSWORD=<strong private password>`
- `ADMIN_EMAIL=<optional>`

Leave these blank/false initially:

- `DERIV_AUTH_TOKEN=`
- `DERIV_APP_ID=`
- `DERIV_ACCOUNT_ID=`
- `DERIV_DEMO_ENABLED=false`

Public research works without Deriv credentials.

## 4. First tests
After deployment:

1. Sign in.
2. Open **Digit Laboratory** and test a currently active synthetic symbol.
3. Open **Probability Radar**, choose a digit contract, and fetch a live proposal.
4. Confirm the page shows ask price, payout, break-even probability, model probability and edge.
5. Run a backtest with an explicitly stated assumed profit-return percentage.

## 5. Demo trading later
Do not paste Deriv PAT/token values into ChatGPT, GitHub, or source code. Put them only into Render environment variables.

When ready to test the Deriv demo account:

- set `DERIV_AUTH_TOKEN`
- set `DERIV_APP_ID` if using PAT authentication
- set the **demo** `DERIV_ACCOUNT_ID`
- set `DERIV_DEMO_ENABLED=true`
- then separately enable **Demo execution** in the app's Risk Settings

The demo trade path requests a short-lived OTP, refuses a `/real?` WebSocket URL, obtains a fresh authenticated proposal, rechecks break-even probability against the model gate, then buys only if the quote still passes.

## 6. Long-run tick collector
On-demand studies can retrieve up to 25,000 recent ticks in pages. For much larger evidence over time, run this command as a separate worker/process:

```bash
python manage.py collect_digits --symbols 1HZ100V --flush 250
```

Add other symbols only after confirming them through Active Symbols and Contracts For. The collector stores aggregated digit counts and transitions rather than every raw tick.

## Safety architecture

- Real trading: hard-disabled in `settings.py`.
- Martingale/stake doubling: not implemented.
- Demo execution: two independent enable switches plus risk gates.
- Every demo order: fresh tick evidence + fresh public proposal + fresh authenticated proposal re-check.
- Daily loss, loss-streak, and trade-count circuit breakers.
