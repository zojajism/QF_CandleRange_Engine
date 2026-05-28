import asyncio
from datetime import datetime
import json
import os
import random
from pathlib import Path
import datetime
import yaml
from dotenv import load_dotenv
from logger_config import setup_logger
from nats.aio.client import Client as NATS
from NATS_setup import ensure_streams_from_yaml
import public_settings
import public_moduls as pm
import public_settings as ps
from indicator_gap_fill import fill_indicator_gap
import strategy_modules as sm
from orders.sl_manager import load_sl_configs


from db_general import get_pg_conn

from telegram_notifier import (
    notify_telegram,
    ChatType,
    start_telegram_notifier,
    close_telegram_notifier,
    ChatType,
)

# --- OANDA exchange adapter (ticks + candles via REST with runtime gap-fill)
from exchange_oanda import (
    get_oanda_tick_stream,
    get_oanda_candles_rest,
    OandaEnv,
)

import buffer_initializer as buffers
from candle_buffer import Keys
from public_settings import load_settings_from_db

CONFIG_PATH = Path("/data/config.yaml")
if not CONFIG_PATH.exists():
    CONFIG_PATH = Path(__file__).resolve().parent / "data" / "config.yaml"

# --- small wrappers to start tasks with a stagger delay
async def _run_ticker_with_stagger(stagger_s: float, **kwargs):
    # tiny offset so symbols don't all hit OANDA at once
    await asyncio.sleep(stagger_s)
    return await get_oanda_tick_stream(**kwargs)

async def _run_candles_with_stagger(stagger_s: float, **kwargs):
    await asyncio.sleep(stagger_s)
    return await get_oanda_candles_rest(**kwargs)


async def main():
    try:


        # Load .env (Docker volume first, then local)
        env_path = Path("/data/.env")
        if not env_path.exists():
            env_path = Path(__file__).resolve().parent / "data" / ".env"
        load_dotenv(dotenv_path=env_path)


        DB_Conn = get_pg_conn()
        logger = setup_logger()
        logger.info(
            json.dumps(
                {
                    "EventCode": 0,
                    "Message": "Starting QF_CandleRange_Engine …",
                }
            )
        )
        await start_telegram_notifier()
        notify_telegram("❇️ QF_CandleRange_Engine App started …", ChatType.ALERT)

        # Load SL configurations from database
        load_sl_configs()

        # Load config
        if not CONFIG_PATH.exists():
            raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")

        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f) or {}

        symbols_cfg = [str(s) for s in config_data.get("symbols", [])]
        timeframes = [str(t).lower() for t in config_data.get("timeframes", [])]  # e.g., ["1m"]

        # Env & credentials
        env_flag = (os.getenv("OANDA_ENV") or "live").strip().lower()
        oanda_env = OandaEnv.LIVE if env_flag == "live" else OandaEnv.PRACTICE

        oanda_token = os.getenv("OANDA_API_TOKEN")
        oanda_account = os.getenv("OANDA_ACCOUNT_ID")

        if not oanda_token or not oanda_account:
            raise RuntimeError("Missing OANDA_API_TOKEN or OANDA_ACCOUNT_ID in .env")


        '''
        # NATS
        
        nats_url = os.getenv("NATS_URL")
        nats_user = os.getenv("NATS_USER")
        nats_pass = os.getenv("NATS_PASS")

        if not nats_url:
            raise RuntimeError("Missing NATS_URL in .env")

        nc = NATS()
        await nc.connect(servers=[nats_url], user=nats_user, password=nats_pass)

        # Ensure streams
        await ensure_streams_from_yaml(nc, "streams.yaml")
        
        '''
        nc = None  # Placeholder since NATS is not currently used; set up when needed

        # Build tasks
        ticker_tasks = []
        candle_tasks = []

        # Use mid-price candles by default; you can add ["B","A"] later if needed
        price_modes = ["M"]

        for i, symbol in enumerate(symbols_cfg):
            instrument = pm._to_oanda_instrument(symbol)

            # stagger per symbol: 0.10s .. 0.30s (with a tiny jitter so they’re not identical)
            base_stagger = min(0.1 + 0.1 * i, 0.3)       # 0.1, 0.2, 0.3, 0.3, ...
            jitter = random.uniform(0.0, 0.03)            # up to +30ms
            stagger_s = base_stagger + jitter

            # Ticker stream (optional; disable adding this task if you don't need ticks now)
            ticker_tasks.append(
                _run_ticker_with_stagger(
                    stagger_s=stagger_s,
                    instrument=instrument,
                    display_symbol=symbol,
                    account_id=oanda_account,
                    token=oanda_token,
                    nc=nc,
                    env=oanda_env,
                    tick_queue=None,
                )
            )

            # Candle fetcher with runtime no-data handling per symbol
            if timeframes:
                candle_tasks.append(
                    _run_candles_with_stagger(
                        stagger_s=stagger_s,
                        display_symbol=symbol,
                        instrument=instrument,
                        timeframes=timeframes,    # e.g., ["1m"]
                        price_modes=price_modes,  # ["M"] now
                        token=oanda_token,
                        nc=nc,
                        env=oanda_env,
                        poll_interval_sec=2,      # small polling loop
                    )
                )


        #System initialization
        symbols = [str(s) for s in config_data.get("symbols", [])]
        timeframes = [str(t) for t in config_data.get("timeframes", [])]
        indicators = [str(t) for t in config_data.get("indicators", [])]  # e.g., ["ATR", "EMA_FAST", "EMA_SLOW", "inBands"]
        HFTs = [str(t) for t in config_data.get("HTF", [])]
        ps.symbol = symbols[0]

        load_settings_from_db()
        public_settings.ExecuteID = 0 # it 's set to 0 for Live executions and Forward Tests; For Backtests, it will be set to the Backtest ID (positive integer) to isolate data per backtest run
        logger.info("Public settings loaded from database.")

        #fill_indicator_gap()
        #logger.info("Indicator gap fill done.")
        
        buffers.init_candle_buffer("OANDA", symbols, timeframes)
        logger.info("Candle buffers initialized for OANDA symbols and timeframes.")
        buffers.init_candle_buffer("OANDA", symbols, HFTs)
        logger.info("Candle buffers initialized for OANDA symbols and HTFs.")

        buffers.init_indicator_buffer(symbols, timeframes, indicators)
        #logger.info("Indicator buffers initialized for OANDA symbols and timeframes.")

        sm.open_sig_registry.bootstrap_from_db(DB_Conn, ps.symbol) 
        open_count = sm.open_sig_registry.get_count() 
        logger.info( json.dumps({ "EventCode": 0, "Message": f"open_sig_registry initialized. open_signals={open_count}" }) )
        #print("AAAAAAAAAAAAAAAA")
        #for sig in open_sig_registry.get_all_signals():
            #print(sig)
        #print("AAAAAAAAAAAAAAAA")
        # Print all open signals loaded from the DB for debugging/inspection

        '''
        # Print CANDLE_BUFFER stats for each symbol/timeframe
        for symbol in symbols:
            for timeframe in timeframes:
                key = Keys(exchange="OANDA", symbol=symbol, timeframe=timeframe)
                buf = buffers.CANDLE_BUFFER.get_or_create(key)
                count = len(buf)
                if count > 0:
                    last_candle = buf[-1]
                    first_candle = buf[0]
                    print(f"CANDLE_BUFFER[{symbol}/{timeframe}] count: {count}, last close_time: {last_candle.get('close_time')}, first close_time: {first_candle.get('close_time')}")
                else:
                    print(f"CANDLE_BUFFER[{symbol}/{timeframe}] is empty.")
        '''
        
        #===================================================================================

        # Run (each item is a coroutine; gather will schedule them concurrently)
        GET_TICK = os.getenv("GET_TICK")

        #if GET_TICK.lower() == "true":
        #await asyncio.gather(*candle_tasks, *ticker_tasks)
        #else:
        await asyncio.gather(*candle_tasks)
        
        #=======================================================================
    finally:
        notify_telegram("⛔️ QF_CandleRange_Engine App stopped.", ChatType.ALERT)
        await close_telegram_notifier()


if __name__ == "__main__":
    asyncio.run(main())
