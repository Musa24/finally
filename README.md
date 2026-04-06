# FinAlly — AI Trading Workstation

A Bloomberg-inspired AI-powered trading workstation with live-streaming market data, simulated portfolio trading, and an LLM chat assistant that can analyze positions and execute trades via natural language.

Built entirely by orchestrated coding agents as the capstone project for an agentic AI coding course.

## Features

- **Live price streaming** via SSE with green/red flash animations
- **Simulated trading** — $10k virtual cash, market orders, instant fills
- **Portfolio visualization** — heatmap (treemap), P&L chart, positions table
- **AI chat assistant** — analyzes portfolio, suggests and auto-executes trades
- **Watchlist management** — 10 default tickers, add/remove manually or via AI

## Architecture

Single Docker container serving everything on port 8000:

- **Frontend**: Next.js (static export) with TypeScript and Tailwind CSS
- **Backend**: FastAPI (Python/uv) with SSE streaming
- **Database**: SQLite with lazy initialization
- **AI**: LiteLLM via OpenRouter (Cerebras inference) with structured outputs
- **Market data**: Built-in simulator (default) or Massive API (optional)

## Quick Start

```bash
# 1. Set up environment
cp .env.example .env
# Edit .env with your OPENROUTER_API_KEY

# 2. Run with Docker
./scripts/start_mac.sh

# 3. Open browser
open http://localhost:8000
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key for LLM chat |
| `MASSIVE_API_KEY` | No | Massive (Polygon.io) key for real market data. Omit to use simulator |
| `LLM_MOCK` | No | Set `true` for deterministic mock LLM responses (testing) |

## Project Structure

```
finally/
├── frontend/          # Next.js static export
├── backend/           # FastAPI uv project
├── planning/          # Project documentation and specs
├── scripts/           # Docker start/stop scripts
├── test/              # Playwright E2E tests
├── db/                # SQLite volume mount (runtime)
└── Dockerfile         # Multi-stage build (Node + Python)
```

## Development

See [planning/PLAN.md](planning/PLAN.md) for the full project specification.

## License

See [LICENSE](LICENSE) for details.
