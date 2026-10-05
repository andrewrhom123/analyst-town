# AI Analyst Pod — Backend

A pod of Claude-powered analysts that track their coverage in near real time, manage trading theses,
meet daily to challenge each other, and answer you on demand, within **$5/day** of Claude spend.

## How it thinks (and what it costs)

| Event | What happens | Model | Cost |
|---|---|---|---|
| Every 30 min, market hours | Finnhub quotes for every covered ticker; files refreshed with the live price | none | $0 |
| Price moves >= 5% since the last thesis, or crosses the thesis' entry zone / target / stop | **Thesis update**: position, signal, entry/exit levels, risk, catalysts, dependencies | Sonnet 5.5 | ~$0.05 |
| New 10-Q / 10-K / 20-F / earnings 8-K (checked 8:00 and 17:00) | **Deep dive**: SEC filings + XBRL, financial model, long-form memo, trading thesis | Opus 5.5 | ~$1 |
| Thesis update flags changed fundamentals | Deep dive queued | Opus 5.5 | ~$1 |
| 16:30 ET weekdays | **Pod meeting**: challenges -> responses + thesis revisions + lessons -> minutes | Sonnet 5.5 | ~$1 |
| You: `/briefing TTD` | Current thinking, assembled from stored state | none | $0, instant |
| You: `/ask TTD: what if earnings miss 20%?` | The covering analyst answers off its model and thesis | Sonnet 5.5 | ~$0.03 |
| You: `/meeting`, `/deepdive TTD`, `/update TTD` | Run now | | as above |

**Budget governor.** Every call is priced from its token usage (`cost_entries`). Autonomous work never
spends into the reserves for the meeting ($1.50, until it has run) and your questions ($0.50); anything
that doesn't fit is deferred to the next day. Claude jobs run one at a time, and deep dives are spaced
at least 60 minutes apart, so agents never all run at once.

**First days.** New coverage gets an initial deep dive (~$1 each). With 10 tickers that's ~$10, so
initial coverage completes over 2-3 days inside the budget (raise `DAILY_BUDGET_USD` temporarily to go faster).

## Coverage

`coverage.yaml` lists analysts and the tickers they cover. Edit it and restart (or `POST /coverage/reload`).
You can also change coverage at runtime without touching code:

```bash
# add a ticker (creates its files and queues the initial deep dive)
curl -X POST $API/coverage/tickers -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"symbol":"STRIPE","name":"Stripe","analyst":"fintech","type":"private","news_query":"Stripe AND (payments OR fintech)"}'
# reassign / pause
curl -X PATCH $API/coverage/tickers/STRIPE -H "X-API-Key: $KEY" -d '{"analyst":"bullish"}' -H "Content-Type: application/json"
```

Ticker types: `public` (SEC + prices + financial model), `private` (news only, no price), `index` (prices, no SEC).

Current roster (4 agents, 27 tickers):
- **Macro Strategist**: SPY, QQQ, TLT, DXY (priced via the UUP ETF)
- **Fintech Analyst**: BLSH, NDAQ, HOOD, COIN, PYPL, XYZ (alias SQ), ADYEN (US ADR ADYEY; not an SEC filer, so no model), MARA
- **Internet Platforms Analyst**: NAVN (alias NVAN), PUBM, TTD, DASH, UBER, ABNB (alias AIRBNB), BKNG (alias BOOKING)
- **AI Analyst**: NVDA, AMD, INTC, MSFT, AMZN, GOOGL, PLTR, AI

Chat commands change coverage without code or redeploys: `/add MSTR to Fintech` (looks the company up,
creates its files, starts tracking, queues the first deep dive), `/remove MARA from Fintech` (archives
files), `/reassign AMZN from AI to Internet Platforms`, `/coverage Fintech`.

## Per-ticker files

```
coverage/TTD/
├── company_memo.md       background, business model, key people + the latest long-form memo
├── latest_events.md      price action, SEC filings, news, upcoming catalysts, recent thesis triggers
├── financial_model.json  annual model, capitalization, football field, scenarios, assumptions
├── trading_thesis.md     live price, signal, position, entry/target/stop, risk, catalysts, dependencies
└── meeting_notes.md      what other analysts said and what changed (last 10 meetings)
```

The database is the source of truth (Railway's disk is wiped on deploy); files are mirrored to
`COVERAGE_DIR` locally and served at `GET /coverage/{ticker}/files/{filename}`. The full Excel model
(Summary/football field, Revenue Build, P&L, DCF, Scenarios, Comps, Model vs. Street, Historical
Tracker, live formulas) is at `GET /coverage/{ticker}/model.xlsx`.

## Deep-dive research format

`GET /coverage/{ticker}/research`:

```json
{
  "long_form_memo": "2-3 page markdown memo (setup, latest print, what changed, model, valuation, risks, conviction, bottom line)",
  "executive_summary": "1-2 paragraph elevator pitch",
  "financial_model": {"revenue_2026E": "$2.81B", "revenue_2027E": "$2.84B", "ebitda_margin": "32.2%", "fcf": "$678.8M", "valuation_range": "$3.0-7.9B"},
  "key_risks": ["..."],
  "conviction_level": 4,
  "reasoning": "...",
  "meta": {...}, "details": {...}
}
```

Claude chooses the drivers and assumptions; Python computes the P&L, DCF, comps, SOTP and scenario values,
so every number in the memo, JSON and Excel ties out. `financial_model` uses the first two forecast years
(`revenue_2026E`/`revenue_2027E` today; the keys roll forward with the calendar). `ebitda_margin` and `fcf`
are for the current forecast year; `valuation_range` is bear-to-bull equity value.

## Commands

```bash
python cli.py /briefing TTD
python cli.py "/ask TTD: What if earnings miss 20%?"
python cli.py /meeting
python cli.py /deepdive NAVN
```

Or `POST /command {"text": "/ask TTD: ..."}` from any client. Other endpoints: `GET /agents`,
`GET /briefing/{ticker}`, `POST /ask/{ticker}`, `GET /meetings/latest`, `GET /jobs`, `GET /costs`,
`GET /status`. Interactive docs at `/docs`.

## Frontend: Analyst Town (`frontend/`)

React + Vite + react-three-fiber. Laptop (>= 1200px): a 3D town in Central Park (lawns, the Lake and Bow
Bridge, the Manhattan skyline) with one house per agent (color-coded, window glow = how fresh the agent's
thinking is) around a town hall; click a house to fly into the agent's office (robot at a three-monitor desk,
one wall monitor per ticker, park-view windows; click a monitor to switch). The town hall, or the glass tower
on Central Park South, opens the **boardroom** (`/boardroom`): the pod around a table overlooking the park,
replaying the latest meeting speaker by speaker, plus "Ask the pod".
Phone/tablet: a swipeable card carousel and a full-screen office with ticker tabs, swipe between tickers,
and a sticky chat dock that rides above the keyboard. three.js is only downloaded on laptop-size screens.

```powershell
cd frontend
npm install
npm run dev        # http://localhost:5173, talks to http://localhost:8000 by default
npm run build      # production build in dist/
```

Chat commands work in every office: `/briefing TTD`, `/ask TTD: ...` (or just type), `/meeting`,
`/add MSTR to Fintech`, `/remove MARA from Fintech`, `/reassign AMZN from AI to Internet Platforms`,
`/coverage Fintech`. Actions that spend credits need the backend's `API_ACCESS_KEY`, entered once under
Settings (gear icon) and stored only in that browser.

### Voice

Agents speak with ElevenLabs (four voice profiles, one per desk: macro, fintech, internet, AI, plus a meeting
chair) and you can talk to them through ElevenLabs Scribe speech-to-text (your tickers and company names are
sent as keyterms so symbols transcribe correctly). One key covers both: set `ELEVENLABS_API_KEY` on the
backend; without it, voice is simply hidden. Keys never reach the browser: the frontend
calls `POST /voice/tts` (returns MP3) and `POST /voice/stt` (raw recording in, `{text}` out), both behind
`API_ACCESS_KEY`. `GET /voice/status` reports what's enabled; `GET /meetings/latest/script` turns the last
meeting into spoken turns for the boardroom.

- Chat: 🎙 mic (auto-sends when you stop talking), ▶ on every reply, "🔊 Voice replies" (read every answer),
  and "🎙 Hands-free" (talk → hear the answer → mic reopens). "Hear the pitch" on each ticker.
- Header 🔈/🔊 toggles synthesized office ambience (keyboards, faster while an agent works).
- Cost control: clips are cached on disk (`VOICE_CACHE_DIR`), so replays are free; new speech is capped at
  `VOICE_DAILY_CHAR_LIMIT` characters per day and `VOICE_MAX_CHARS` per clip (cut at a sentence end).
- Voices are checked against your account (`GET /v1/voices`, needs the key's Voices read permission): if a
  preferred voice isn't in your account, an unused voice of the same gender stands in, and `GET /voice/status`
  shows which voice each desk got. Pin one with `ELEVENLABS_VOICE_MACRO` / `_FINTECH` / `_INTERNET` / `_AI` /
  `_CHAIR` (any voice ID in your account).

Deploy to Vercel: import the repo with **Root Directory = `frontend`** (framework preset Vite), and set
`REACT_APP_API_URL=https://<your-railway-app>.up.railway.app`. `vercel.json` handles client-side routes.
On the backend set `CORS_ORIGINS=https://<your-app>.vercel.app`.

## Local development

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # fill in keys, including FINNHUB_API_KEY
.\.venv\Scripts\uvicorn main:app --reload
```

## Deploy to Railway

1. Push to GitHub (`.env`, `*.db` and `coverage/` are git-ignored).
2. Railway: New Project -> Deploy from GitHub repo; add a **PostgreSQL** service (injects `DATABASE_URL`).
3. Variables: `ANTHROPIC_API_KEY`, `ALPHAVANTAGE_API_KEY`, `NEWSAPI_KEY`, `FINNHUB_API_KEY`,
   `SEC_USER_AGENT`, `API_ACCESS_KEY`, `ENVIRONMENT=production`.
4. Generate a domain, then `curl https://<app>.up.railway.app/status`.

The scheduler runs inside the web process by default. To split it out, add a worker service with
`python -m scheduler.jobs` and set `ENABLE_SCHEDULER=false` on the web service.

## Data sources and limits

* **Finnhub (free):** 60 calls/min; ~10 symbols every 30 min is well within it.
* **Alpha Vantage (free, 25 calls/day):** now only used inside deep dives (fundamentals, macro series,
  comps multiples). Comps multiples are cached 7 days and fetched within `ALPHAVANTAGE_PEER_BUDGET`.
* **NewsAPI (free developer plan):** 100 requests/day; terms limit it to development use, so production
  needs a paid plan or another source.
* **SEC EDGAR:** free; `SEC_USER_AGENT` must contain a contact email.
* Bullish files 20-F/6-K and reports gross digital-asset sales as revenue; the model uses adjusted revenue.
* Signals and levels are model output for your research process, not investment advice.
