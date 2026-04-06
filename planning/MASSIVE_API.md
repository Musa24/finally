# Massive API Reference (formerly Polygon.io)

Polygon.io rebranded to **Massive** on October 30, 2025. The new base URL is `https://api.massive.com`; the legacy `https://api.polygon.io` still works during the transition.

## Authentication

**Query parameter (preferred):**
```
GET https://api.massive.com/v2/...?apiKey=YOUR_API_KEY
```

**Header:**
```
Authorization: Bearer YOUR_API_KEY
```

## Rate Limits

| Tier | Limit |
|------|-------|
| Free | 5 requests/minute |
| Paid | Unlimited (stay under ~100/s) |

For FinAlly: free tier polls every 15s, paid tiers every 2-5s.

---

## Primary Endpoint: Snapshot (Batch Latest Prices)

This is the main endpoint for FinAlly. One call returns latest prices for all watched tickers.

```
GET /v2/snapshot/locale/us/markets/stocks/tickers
```

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `tickers` | No | Comma-separated ticker list (e.g. `AAPL,GOOGL,MSFT`). Omit for all tickers. |
| `apiKey` | Yes | API key |

### Example Request

```
GET https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,GOOGL,MSFT&apiKey=KEY
```

### Response

```json
{
  "count": 3,
  "status": "OK",
  "tickers": [
    {
      "ticker": "AAPL",
      "todaysChange": 1.25,
      "todaysChangePerc": 0.65,
      "updated": 1704067200000000000,
      "day": {
        "o": 192.50,
        "h": 194.76,
        "l": 191.83,
        "c": 193.75,
        "v": 48123456,
        "vw": 193.12
      },
      "prevDay": {
        "o": 191.20,
        "h": 193.10,
        "l": 190.85,
        "c": 192.50,
        "v": 52345678,
        "vw": 192.01
      },
      "lastTrade": {
        "p": 193.75,
        "s": 50,
        "t": 1704067198000000000
      },
      "lastQuote": {
        "p": 193.74,
        "s": 200,
        "P": 193.76,
        "S": 100,
        "t": 1704067199000000000
      }
    }
  ]
}
```

### Key Fields

| Field | Description |
|-------|-------------|
| `lastTrade.p` | Last trade price — **use this as current price** |
| `day.c` | Current day's close (updates intraday) |
| `prevDay.c` | Previous day's close |
| `todaysChange` | Absolute price change from previous close |
| `todaysChangePerc` | Percentage change from previous close |
| `updated` | Last update timestamp (Unix nanoseconds) |
| `day.o/h/l/c/v/vw` | Today's OHLCV and VWAP |

---

## Secondary Endpoint: Previous Close

For end-of-day data on a single ticker.

```
GET /v2/aggs/ticker/{ticker}/prev
```

### Example

```
GET https://api.massive.com/v2/aggs/ticker/AAPL/prev?apiKey=KEY
```

### Response

```json
{
  "adjusted": true,
  "status": "OK",
  "ticker": "AAPL",
  "resultsCount": 1,
  "results": [
    {
      "T": "AAPL",
      "o": 115.55,
      "h": 117.59,
      "l": 114.13,
      "c": 115.97,
      "v": 131704427,
      "vw": 116.3058,
      "t": 1605042000000
    }
  ]
}
```

---

## Python Code Example

```python
import httpx

BASE_URL = "https://api.massive.com"


async def fetch_snapshots(
    api_key: str,
    tickers: list[str],
) -> dict:
    """Fetch latest price snapshots for multiple tickers in one call."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers",
            params={"tickers": ",".join(tickers), "apiKey": api_key},
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()


def parse_snapshot(ticker_data: dict) -> dict:
    """Extract the fields FinAlly needs from a single ticker snapshot."""
    return {
        "ticker": ticker_data["ticker"],
        "price": ticker_data["lastTrade"]["p"],
        "previous_close": ticker_data["prevDay"]["c"],
        "change": ticker_data["todaysChange"],
        "change_pct": ticker_data["todaysChangePerc"],
        "updated_ns": ticker_data["updated"],
    }
```

### Usage

```python
data = await fetch_snapshots(api_key, ["AAPL", "GOOGL", "MSFT"])
for t in data["tickers"]:
    info = parse_snapshot(t)
    print(f"{info['ticker']}: ${info['price']:.2f} ({info['change_pct']:+.2f}%)")
```

---

## Error Handling

| Status | Meaning |
|--------|---------|
| 200 | Success |
| 401 | Invalid or missing API key |
| 403 | Insufficient subscription tier |
| 429 | Rate limit exceeded |
| 500+ | Massive server error — retry with backoff |

On 429, back off and retry. On 401/403, fall back to the simulator gracefully.

---

## FinAlly Integration Summary

- Use the **batch snapshot endpoint** — one call covers all watchlist tickers
- Extract `lastTrade.p` as the current price, `prevDay.c` as previous close
- Free tier: poll every 15s. Paid: poll every 2-5s.
- The response contains everything needed: price, change, change %, previous close
- No WebSocket needed — REST polling is simpler and works on all Massive tiers
