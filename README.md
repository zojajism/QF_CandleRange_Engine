# QF_RAEP_Engine

`QF_RAEP_Engine` is a live FX trading engine built around a **regime-aware EMA pullback strategy**.

At a high level, it:
- reads market data from **OANDA**
- checks whether market conditions are suitable for trading
- waits for pullbacks toward an EMA area
- looks for continuation signals in the trend direction
- sends orders and tracks open trades

## What the service does

The engine continuously:
- loads runtime settings from the database
- restores recent candles and indicators into memory
- receives new candles and ticks from OANDA
- calculates strategy state such as trend, volatility, and pullback context
- places broker orders when a valid setup is detected
- syncs order status and trade outcomes back to storage
- sends Telegram notifications for important events

## Main runtime pieces

This project expects these external services/configuration sources to be available:
- **OANDA** account credentials
- **PostgreSQL** for candles, settings, indicators, and signals
- **NATS** connection details
- **Telegram** bot settings for notifications
- runtime config in `config.yaml`
- environment variables in `.env`

The application first looks for mounted runtime files under `/data`, and falls back to local files under `src/data` when available.

## Run

The workspace includes a `Makefile` with the main commands:

```bash
make install
make run
```

## Notes

- Entry point: `src/main.py`
- Default run command from the `Makefile`: `uv run python -m src.main`
- This repository is structured as a long-running service rather than a standalone backtest notebook or library

