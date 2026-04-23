# Cloudflare Worker — stock-daily-brief proxy

A tiny worker that lets the static dashboard pull fresh Yahoo Finance quotes
+ Google News RSS on demand (bypassing browser CORS).

## Setup (one-time, ~10 min)

### 1. Sign up for Cloudflare (free, no credit card)

https://dash.cloudflare.com/sign-up

### 2. Install Wrangler CLI

```bash
# macOS with Homebrew (recommended)
brew install cloudflare-wrangler2

# OR via npm (if you have Node)
npm install -g wrangler
```

### 3. Log in

```bash
wrangler login
```

This opens your browser; click "Allow" to link the CLI to your CF account.

### 4. Deploy

```bash
cd cf-worker
wrangler deploy
```

You'll see output like:

```
Uploaded stock-daily-brief-proxy (1.23 sec)
Published stock-daily-brief-proxy
  https://stock-daily-brief-proxy.YOUR-USERNAME.workers.dev
```

Copy that URL.

### 5. Tell the dashboard about the worker

Open `portfolio.yaml` and set:

```yaml
cf_worker_url: "https://stock-daily-brief-proxy.YOUR-USERNAME.workers.dev"
```

Then rebuild:

```bash
source .venv/bin/activate
python build_dashboard.py
git add portfolio.yaml docs/
git commit -m "Wire dashboard to CF Worker"
git push
```

The REFRESH button on the dashboard will now work.

### 6. (Optional) Enable the FULL REBUILD button

This lets a site button trigger the GitHub Actions workflow_dispatch, so you can
force a full daily-brief rebuild from the dashboard.

1. Create a GitHub PAT: https://github.com/settings/tokens?type=beta
   - Repository access: just `stock-daily-brief`
   - Permissions: `Actions: Read and write`, `Contents: Read and write`, `Metadata: Read`
   - Copy the token

2. Set secrets on the Worker:

```bash
wrangler secret put GH_PAT
# paste token when prompted

wrangler secret put GH_REPO
# enter: iannn211/stock-daily-brief
```

3. Redeploy if you changed the code:

```bash
wrangler deploy
```

## Testing

```bash
curl https://stock-daily-brief-proxy.YOUR-USERNAME.workers.dev/health
# → {"ok":true,"service":"stock-daily-brief-proxy","version":"1.0"}

curl "https://stock-daily-brief-proxy.YOUR-USERNAME.workers.dev/quote?symbols=2330.TW,NVDA"
# → {"fetched_at":..., "count":2, "quotes":[...]}
```

## Endpoints

| Route | Method | Purpose |
|-------|--------|---------|
| `/health` | GET | Liveness check |
| `/quote?symbols=a,b,c` | GET | Yahoo Finance quotes (max 200 symbols) |
| `/news?q=台積電` | GET | Google News RSS (zh-TW) |
| `/rebuild` | POST | Trigger GH Actions workflow (needs GH_PAT secret) |

## Costs

- CF Workers free tier: 100k requests/day, 10ms CPU/req
- Personal dashboard use: ~100 req/day expected
- You will not pay anything

## Updating the worker

Edit `src/index.js`, then:

```bash
wrangler deploy
```

Takes ~10 seconds.

## Troubleshooting

**`wrangler login` opens a weird URL**
That's normal — it's a local callback. Click Allow on the CF page.

**`401 Unauthorized` on deploy**
Run `wrangler logout && wrangler login` again.

**REFRESH button shows "CORS error"**
Your GitHub Pages URL isn't in `ALLOWED_ORIGINS` in `src/index.js`. Edit and redeploy.

**Yahoo returns 401/429**
Yahoo rate-limits unofficial API clients. The worker caches 30s to minimize hits.
If you see this often, bump `QUOTE_TTL` in `src/index.js`.
