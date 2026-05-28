from decimal import Decimal
import json
import logging
from os import truncate
from typing import Any, Dict, List
from urllib import response
from venv import logger
from datetime import datetime
import buffer_initializer

import requests

from db_general import _insert_signals
from orders.order_executor import sync_broker_orders
from candle_buffer import Keys
from indicator_buffer import IndicatorKey 
import strategy_modules as sm
import buffer_initializer as buffers
import public_moduls as pm 
import public_settings as ps 
from telegram_notifier import (
    notify_telegram,
    ChatType,
    start_telegram_notifier,
    close_telegram_notifier,
    ChatType,
)
from orders.order_executor import send_market_order, OrderExecutionResult, update_account_summary
from signals import open_signal_registry
from db_general import get_pg_conn
from orders.order_executor import get_account_summary, print_account_summary

logger = logging.getLogger(__name__)


def run_engine(candle_body: Dict[str, Any]):
    '''
    "exchange": exchange,
    "symbol": payload_symbol,
    "base_currency": base_currency.upper(),
    "quote_currency": quote_currency.upper(),
    "timeframe": timeframe,
    "open_time": open_time_iso,
    "close_time": close_time_iso,
    "open": float(data["open"]),
    "high": float(data["high"]),
    "low": float(data["low"]),
    "close": float(data["close"]),
    "volume": float(data["volume"]),
    # Publisher insert timestamp (UTC ISO)
    "insert_ts": _ensure_utc_iso(datetime.now(timezone.utc)),
    '''
    if isinstance(candle_body.get("open_time"), str):
        candle_body["open_time"] = datetime.fromisoformat(candle_body["open_time"])
    if isinstance(candle_body.get("close_time"), str):
        candle_body["close_time"] = datetime.fromisoformat(candle_body["close_time"])
    
    # Check if the received candle is newer than the last candle in the buffer to avoid processing duplicates
    key = Keys(exchange=candle_body["exchange"], symbol=candle_body["symbol"], timeframe=candle_body["timeframe"])
    candles = buffer_initializer.CANDLE_BUFFER.last_n(key, 1)
    if candles and candles[0]["close_time"] == candle_body["close_time"]:
        logger.warning(f"SKIP PROCESSING THE CANDLE. Received candle with close_time {candle_body['close_time']}")
        return 
    #==============================================================================================================

    symbol = candle_body["symbol"]
    timeframe = candle_body["timeframe"] 
    exchange = candle_body["exchange"]   
    close_time = candle_body["close_time"]
    close = Decimal(str(round(candle_body["close"], 5)))

    sm.symbol = symbol
    sm.timeframe = timeframe
    sm.exchange = exchange
    sm.close_time = close_time

    sm.Candle_Count_After_HTF_Reset += 1

    logger.info(f"Candle received: {sm.symbol}, {sm.timeframe}, {pm.format_time_simple(str(sm.close_time))}, {close}, Candle_counter: {sm.Candle_Count_After_HTF_Reset}")

    # Append candle to the list =============================================================================================
    key = Keys(sm.exchange, sm.symbol, sm.timeframe)
    buffers.CANDLE_BUFFER.append(key, candle_body)
    #========================================================================================================================
    
    #Manage HTF
    sm.manage_HTF()

    #=========================================================================================================================

    # Call engine modules to calculate engine parameters ====================================================================
    '''
    sm.calculate_ATR(sm.timeframe)
    sm.calculate_MACD()
    sm.calculate_EMA(speed="fast")
    sm.calculate_EMA(speed="slow")
    sm.calculate_RSI()
    sm.calculate_ADX(sm.timeframe)
    sm.calc_norm_slope()
    sm.check_trend()
    sm.check_close_in_Keltner_Bands()
    sm.is_bearish_engulfing_candle()
    sm.is_bullish_engulfing_candle()
    sm.is_hammer_candle()
    sm.is_shooting_star_candle()
    sm.is_3candle_strike_bullish()
    sm.is_3candle_strike_bearish()
    sm.calculate_Chandelier_Exit()
    sm.calculate_Volume_AVG()
    sm.update_pivot_buffer(sm.timeframe)
    '''
    #========================================================================================================================



    # Decision Engine
    order_is_allowed = True
    if pm.validate_trading_hours(sm.close_time) == False:
        order_is_allowed = False

    if sm.Candle_Count_After_HTF_Reset != 1:
        order_is_allowed = False

    # if all general conditions are met, then check strategy specific conditions and send order
    if order_is_allowed:
        
        try:
            valid_signal, side, TP, SL = sm.is_valid_signal_HTF_Range()
        except Exception as e:
            logger.error(f"Error in strategy signal detection: {e}")
            valid_signal = False
        
        if valid_signal:
            target_pips = abs(close - Decimal(TP)) * 10000
            sl_pips = abs(close - Decimal(SL)) * 10000
            #------------------------------------------------
            if sl_pips <= 0:
                logger.warning("Invalid sl_pips=%s. Skip order sizing.", sl_pips)
                return
            
            try:
                account_summary = get_account_summary()
                account = account_summary.get("account", {})
                account_balance = Decimal(account.get("balance", 0))
                available_margin = Decimal(account.get("marginAvailable", 0))
            except Exception as e:
                logger.error(f"Error in fetching account summary: {e}")
                return
            
            try:
                result = pm.calculate_single_position_size(
                        sl_pips=sl_pips,
                        position_price=close,
                        account_balance=account_balance,
                        available_margin=available_margin,
                        risk_percent=ps.Risk_Percent,
                        risk_cap_dollar=ps.Risk_Cap_Dollar,
                        )
            except Exception as e:
                logger.error(f"Error in position sizing calculation: {e}")
                return
            
            trade_skipped = result["trade_skipped"]
            order_units = result["position_size"]
            required_margin = result["margin_required"]
            actual_risk_amount = result["risk_value"]
            profit_est = result["tp_value"]

            if order_units <= 0 or trade_skipped:
                logger.warning(
                    f"Signal skipped! side={side}, target_pips={target_pips:.1f}, sl_pips={sl_pips:.1f}, "
                    f"order_units={order_units}, required_margin={required_margin:.2f}, risk_est={actual_risk_amount:.2f}, "
                    f"profit_est={profit_est:.2f}, TP={TP}, SL={SL}, available_margin={available_margin:.2f}"
                )
                return
            #------------------------------------------------
            logger.info(
                f"Valid signal detected! side={side}, target_pips={target_pips:.1f}, sl_pips={sl_pips:.1f}, "
                f"order_units={order_units}, required_margin={required_margin:.2f}, risk_est={actual_risk_amount:.2f}, "
                f"profit_est={profit_est:.2f}, TP={TP}, SL={SL}, available_margin={available_margin:.2f}"
            )


            try:
                instrument = pm._to_oanda_instrument(sm.symbol)
                client_order_id = (
                    f"qf-{close_time.strftime('%Y%m%d%H%M%S')}-"
                    f"{instrument.replace('_', '')}"
                )

                logger.info(
                            f"[ORDER] Sending market order: instrument={instrument}, side={side}, units={order_units}, "
                            f"tp_price={TP}, sl_price={SL}"
                        )

                order_info = send_market_order(
                    symbol=instrument,
                    side=side,
                    units=order_units,
                    tp_price=TP,
                    sl_price=SL,
                    client_order_id=client_order_id,
                )
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else "?"
                body = None
                if e.response is not None:
                    try:
                        body = e.response.json()
                    except Exception:
                        body = e.response.text
                    notify_telegram(body, ChatType.ALERT)

                logger.info(f"[ORDER] HTTP error {status} from OANDA: {body}")
                order_info = None


            if order_info is None:
                logger.info(
                    f"[ORDER] send_market_order returned None for {symbol} "
                    f"(side={side}, tp_price={TP})"
                )
            else:

                cancel = None
                raw = getattr(order_info, "raw_response", None)
                if isinstance(raw, dict):
                    cancel = raw.get("orderCancelTransaction")

                if cancel:
                    reason = cancel.get("reason")
                    logger.error(f"[ORDER] Broker cancelled order. reason={reason}")

                    # Optional telegram for cancel
                    try:

                        msg = (
                            "🆑 Order cancelled\n"
                            f"Reason:         {reason}\n\n"
                            f"Symbol:         {symbol}\n"
                            f"Side:           {side.upper()}\n"
                            f"Entry price:    {pm.truncate(close,5)}\n"
                            f"Target price:   {pm.truncate(TP,5)}\n"
                            f"Distance:       {pm.truncate(target_pips,2)} pips\n"
                            f"SL_Price:       {pm.fmt(SL,5)}\n"
                            f"Est. Profit:    ${pm.truncate(profit_est,2)}\n\n"
                            f"Event time:     {close_time.strftime('%Y-%m-%d %H:%M')}\n\n"
                        )
                        notify_telegram(msg, ChatType.INFO)
                    except Exception as te:
                        logger.info(f"[WARN] telegram cancel notify failed: {te}")
                else:
                    try:
                        actual_target_pips = abs(TP - order_info.actual_entry_price) / pm._pip_size(symbol) if order_info else None
                        msg = (
                            "⚡ Broker Order\n"
                            f"Symbol:         {symbol}\n"
                            f"Side:                 {side.upper()}\n"
                            f"Entry price:        {pm.truncate(close,5)}\n"
                            f"Target price:      {pm.truncate(TP,5)}\n"
                            f"SL_Price:            {pm.fmt(SL,5)}\n"
                            f"Actual TP Pips:   {pm.truncate(actual_target_pips,2) if actual_target_pips is not None else 'N/A'}\n"
                            f"Est. Profit:       ${pm.truncate(profit_est,2)}\n"
                            f"required_margin:         ${pm.truncate(required_margin,2)}\n\n"
                            f"Event time:     {close_time.strftime('%Y-%m-%d %H:%M')}\n\n"
                        )
                        notify_telegram(msg, ChatType.INFO)
                    except Exception as e:
                        logger.error(f"[WARN] telegram notify failed: {e}")
    

            # Handle Signals in DB

            actual_target_pips = target_pips
            batch_rows_signals: List[tuple] = []
            batch_rows_signals.append((
                close_time,             # event_time (trigger)
                symbol,                 # signal_symbol
                "",                     # confirm_symbols
                side.lower(),           # position_type
                "",                     # price_source
                close,                  # position_price
                target_pips,            # target_pips (ADJUSTED, magnitude)
                TP,                     # target_price (ADJUSTED)
                "",                     # ref_symbol (context)
                "",                     # ref_type (context)
                close_time,             # pivot_time (ref anchor)
                close_time,             # found_at (target pivot time)
                "",                     # reject_reason
                0.0,                    # spread
                sl_pips,                # sl_pips
                SL,                     # sl_price
                "",                     # correlation_summary
                True,                    # order_sent


                order_info.order_sent_time,   # order_sent_time
                order_info.broker_order_id,   # broker_order_id
                order_info.broker_trade_id,   # broker_trade_id
                order_info.units,             # order_units
                order_info.actual_entry_time, # actual_entry_time
                order_info.actual_entry_price,# actual_entry_price
                TP,   # actual_tp_price
                actual_target_pips,           # actual_target_pips
                order_info.status,            # order_status
                order_info.exec_latency_ms,   # exec_latency_ms
                order_info.lastTransactionID,          # lastTransactionID from OANDA response
                actual_risk_amount,          # sl_value
                profit_est,                  # tp_value
                account_balance,             # balance_before
                available_margin,            # available_margin_before
                required_margin,             # required_margin
            ))
            _insert_signals(batch_rows_signals)


    # End of : "if all general conditions are met, then check strategy specific conditions and send order"
    else:
        i = 0
        logger.info("Order not allowed due to trading hours or other general conditions.")  
    #=====================================================================================================================

    # Sync broker orders to update order status and account summary
    sync_broker_orders(symbol)
    
    print_account_summary()


    #open_count = sm.open_sig_registry.get_count() 
    #logger.info(json.dumps({ "EventCode": 0, "Message": f"open_sig_registry initialized. open_signals={open_count}" }) )
    
    #flush open signal registry to db
    #sm.open_sig_registry.flush_distance_metrics(get_pg_conn())
    
    #update open signal registry from db
    sm.open_sig_registry.bootstrap_from_db(get_pg_conn(), ps.symbol) 
    open_count = sm.open_sig_registry.get_count() 
    logger.info(json.dumps({ "EventCode": 0, "Message": f"open_sig_registry initialized. open_signals={open_count}" }) )
      
    # Records values and history
    sm.record_strategy_modules_history(sm.timeframe)
    #========================================================================================================================
