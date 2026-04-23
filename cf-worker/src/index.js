/**
 * stock-daily-brief — Cloudflare Worker proxy
 *
 * Free-tier Cloudflare Worker that proxies Yahoo Finance + Google News RSS
 * so the static GitHub Pages dashboard can fetch fresh data on demand
 * (the browser can't call those origins directly — CORS + UA restrictions).
 *
 * Endpoints:
 *   GET  /health                        → {ok: true}
 *   GET  /quote?symbols=2330.TW,NVDA   → {fetched_at, quotes: [...]}
 *   GET  /news?q=台積電                 → Google News RSS XML
 *   POST /rebuild                       → trigger GH Actions (requires GH_PAT secret)
 *
 * Limits:
 *   - 100k req/day on CF free tier (personal use: ~100 req/day expected)
 *   - /quote cached 30s edge-side; /news cached 5min
 *   - CORS locked to github.io + localhost
 */

const ALLOWED_ORIGINS = [
  'https://iannn211.github.io',
  'http://localhost:8000',
  'http://127.0.0.1:8000',
  'http://localhost:8080',
  'http://127.0.0.1:8080',
  'null',  // file:// origin for local preview
];

const QUOTE_TTL = 30;   // seconds — Yahoo refreshes ~every 15-20s during market hours
const NEWS_TTL = 300;   // seconds — 5 min is fine for RSS

function corsHeaders(origin) {
  const allow = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  return {
    'Access-Control-Allow-Origin': allow,
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    'Access-Control-Max-Age': '86400',
    'Vary': 'Origin',
  };
}

function json(body, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      ...extraHeaders,
    },
  });
}

// Yahoo Finance changed /v7/quote to require crumb+cookie auth in 2024.
// We use /v8/chart instead — same data (meta block has all regularMarket* fields),
// no auth needed, but 1 request per symbol (CF free tier: 50 subrequests/invocation).
const YAHOO_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36';

async function fetchOneQuote(symbol) {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?interval=1d&range=2d`;
  try {
    const resp = await fetch(url, {
      headers: {
        'User-Agent': YAHOO_UA,
        'Accept': 'application/json',
      },
      cf: { cacheTtl: QUOTE_TTL, cacheEverything: true },
    });
    if (!resp.ok) return { symbol, error: `yahoo ${resp.status}` };
    const data = await resp.json();
    const result = data?.chart?.result?.[0];
    if (!result) {
      const errMsg = data?.chart?.error?.description || 'no data';
      return { symbol, error: errMsg };
    }
    const meta = result.meta || {};
    const price = meta.regularMarketPrice;
    const prevClose = meta.chartPreviousClose ?? meta.previousClose;
    const change = (price != null && prevClose != null) ? price - prevClose : null;
    const changePct = (change != null && prevClose) ? (change / prevClose) * 100 : null;
    return {
      symbol: meta.symbol || symbol,
      price,
      prev_close: prevClose,
      day_change: change,
      day_change_pct: changePct,
      currency: meta.currency,
      market_state: meta.marketState,
      high: meta.regularMarketDayHigh,
      low: meta.regularMarketDayLow,
      high_52w: meta.fiftyTwoWeekHigh,
      low_52w: meta.fiftyTwoWeekLow,
      as_of: meta.regularMarketTime,
      exchange: meta.exchangeName,
    };
  } catch (e) {
    return { symbol, error: `fetch error: ${e.message}` };
  }
}

async function handleQuote(url) {
  const symbols = url.searchParams.get('symbols');
  if (!symbols) {
    return json({ error: 'missing symbols param' }, 400);
  }

  // Sanitize: only letters, digits, dot, hyphen, comma, caret, equals
  if (!/^[A-Za-z0-9.,\-\^=]+$/.test(symbols)) {
    return json({ error: 'invalid symbols format' }, 400);
  }

  const list = symbols.split(',').filter(Boolean);
  // CF free tier: 50 subrequests per invocation. Leave headroom for health/news fan-out.
  if (list.length > 45) {
    return json({ error: 'too many symbols (max 45 per call due to CF subrequest limit)' }, 400);
  }

  // Fan out in parallel — all requests fire at once, await all.
  const results = await Promise.allSettled(list.map(fetchOneQuote));
  const quotes = [];
  const errors = [];
  for (const r of results) {
    if (r.status === 'fulfilled') {
      if (r.value.error) errors.push(`${r.value.symbol}: ${r.value.error}`);
      else quotes.push(r.value);
    } else {
      errors.push(`rejected: ${r.reason?.message || 'unknown'}`);
    }
  }

  return json({
    fetched_at: Math.floor(Date.now() / 1000),
    count: quotes.length,
    quotes,
    errors: errors.length ? errors : undefined,
  }, 200, {
    'Cache-Control': `public, max-age=${QUOTE_TTL}`,
  });
}

async function handleNews(url) {
  const q = url.searchParams.get('q');
  if (!q) return json({ error: 'missing q param' }, 400);
  if (q.length > 200) return json({ error: 'q too long' }, 400);

  const rss = `https://news.google.com/rss/search?q=${encodeURIComponent(q)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant`;
  const resp = await fetch(rss, {
    headers: {
      'User-Agent': 'Mozilla/5.0 (compatible; stock-daily-brief/1.0)',
    },
    cf: { cacheTtl: NEWS_TTL, cacheEverything: true },
  });
  const xml = await resp.text();
  return new Response(xml, {
    status: resp.status,
    headers: {
      'Content-Type': 'application/xml; charset=utf-8',
      'Cache-Control': `public, max-age=${NEWS_TTL}`,
    },
  });
}

async function handleRebuild(request, env) {
  if (!env.GH_PAT || !env.GH_REPO) {
    return json({
      error: 'rebuild disabled — set GH_PAT + GH_REPO secrets in CF worker',
    }, 503);
  }

  const body = await request.json().catch(() => ({}));
  const workflow = body.workflow || 'daily-brief-light.yml';

  const resp = await fetch(
    `https://api.github.com/repos/${env.GH_REPO}/actions/workflows/${workflow}/dispatches`,
    {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${env.GH_PAT}`,
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'stock-daily-brief-worker',
      },
      body: JSON.stringify({ ref: 'main' }),
    }
  );

  if (resp.status === 204) {
    return json({ ok: true, workflow, message: 'workflow triggered' });
  }
  const errText = await resp.text();
  return json({ error: `github ${resp.status}: ${errText}` }, 502);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = request.headers.get('Origin') || '';
    const cors = corsHeaders(origin);

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: cors });
    }

    let response;
    try {
      if (url.pathname === '/health' || url.pathname === '/') {
        response = json({ ok: true, service: 'stock-daily-brief-proxy', version: '1.0' });
      } else if (url.pathname === '/quote' && request.method === 'GET') {
        response = await handleQuote(url);
      } else if (url.pathname === '/news' && request.method === 'GET') {
        response = await handleNews(url);
      } else if (url.pathname === '/rebuild' && request.method === 'POST') {
        response = await handleRebuild(request, env);
      } else {
        response = json({ error: 'not found' }, 404);
      }
    } catch (err) {
      response = json({ error: `worker error: ${err.message}` }, 500);
    }

    // Merge CORS headers onto response
    const headers = new Headers(response.headers);
    for (const [k, v] of Object.entries(cors)) {
      headers.set(k, v);
    }
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  },
};
