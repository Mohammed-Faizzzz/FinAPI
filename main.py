import os
import httpx
import redis.asyncio as redis
from fastapi import FastAPI, Request
from slowapi.errors import RateLimitExceeded
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from typing import List
import asyncio
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Configure Redis (default localhost:6379)
redis_client = redis.from_url("redis://localhost:6379", decode_responses=True)

# Example external API config (Alpha Vantage)
ALPHA_VANTAGE_URL = os.getenv("ALPHA_VANTAGE_URL")
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")

limiter = Limiter(key_func=get_remote_address)
app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

async def fetch_stock_price(symbol: str) -> dict:
    """
    Fetch stock data from Alpha Vantage and normalize it.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(ALPHA_VANTAGE_URL, params={
            "function": "GLOBAL_QUOTE",
            "symbol": symbol,
            "apikey": ALPHA_VANTAGE_API_KEY
        })
        data = resp.json()
    
    # Normalize response
    if "Global Quote" not in data:
        return {"symbol": symbol, "price": None, "error": "No data"}

    quote = data["Global Quote"]
    normalized = {
        "symbol": quote["01. symbol"],
        "price": float(quote["05. price"]),
        "volume": int(quote["06. volume"]),
        "timestamp": quote["07. latest trading day"]
    }
    return normalized

@app.get("/stock/{symbol}")
@limiter.limit("5/minute")
async def get_stock(symbol: str, request: Request):
    key = f"stock:{symbol.upper()}"

    # Check Redis cache
    cached = await redis_client.get(key)
    if cached:
        return {"symbol": symbol, "cached": True, "data": eval(cached)}

    # Cache miss → fetch from API
    data = await fetch_stock_price(symbol)

    # Save to Redis with TTL (e.g. 60s)
    await redis_client.set(key, str(data), ex=60)

    return {"symbol": symbol, "cached": False, "data": data}

@app.get("/batch")
@limiter.limit("5/minute")
async def get_batch(symbols: str, request: Request):
    """
    Example: /batch?symbols=AAPL,MSFT,TSLA
    """
    tickers = [s.strip().upper() for s in symbols.split(",")]

    async def get_one(symbol):
        key = f"stock:{symbol}"
        cached = await redis_client.get(key)
        if cached:
            return {"symbol": symbol, "cached": True, "data": eval(cached)}

        data = await fetch_stock_price(symbol)
        await redis_client.set(key, str(data), ex=60)
        return {"symbol": symbol, "cached": False, "data": data}

    results = await asyncio.gather(*(get_one(sym) for sym in tickers))
    return {"results": results}
