"""持仓状态感知 + 止盈(规范第8章止盈 / 第9章持仓感知)。

同一信号对持有者与空仓者意义不同:空仓主推买入,持有主推卖出/止盈。本模块不改打分/
信号本身,只在其上做**个性化路由与翻译**(规范9.2),并补齐持仓后的止盈逻辑(规范8.2/8.4)。

止盈(持有时):
  trailing_tp = 持仓期最高 ×(1−回撤);effective_exit = max(ATR吊灯止损, trailing_tp);
  浮盈巨大(>50%)收紧回撤保护利润;TP1/TP2/TP3 分级并入卖出体系(SELL-TP 区别于止损 SELL)。
"""
import pandas as pd


def annotate(stock, edf, sig, cfg) -> dict:
    """据持仓状态个性化。返回增补字段(your_pnl/trailing_tp/effective_exit/tp_level/action_for_you/signal_type)。"""
    status = (stock.position_status if stock else "EMPTY") or "EMPTY"
    close = float(edf["close"].iloc[-1])
    tp_cfg = getattr(cfg, "take_profit", {}) or {}
    out = {
        "position_status": status,
        "cost_price": (float(stock.cost_price) if stock and stock.cost_price else 0.0),
        "your_pnl": None, "trailing_tp": None, "effective_exit": None,
        "tp_level": None, "action_for_you": None, "signal_type": None,
    }

    pnl = None
    if status == "HOLDING" and out["cost_price"] > 0:
        pnl = round((close - out["cost_price"]) / out["cost_price"] * 100, 1)
        out["your_pnl"] = pnl

    if status == "HOLDING":
        dd = float(tp_cfg.get("trailing_drawdown_tech", 0.18))
        if pnl is not None and pnl > 50:                          # 浮盈巨大→收紧保护利润
            dd = float(tp_cfg.get("trailing_drawdown_stable", 0.12))
        hi = None
        if stock and stock.entry_date:
            sub = edf[edf["date"] >= pd.Timestamp(stock.entry_date)]
            if len(sub):
                hi = float(sub["high"].max())                     # 持仓期最高
        if hi is None:
            hi = float(edf["high"].tail(60).max())
        tp = round(hi * (1 - dd), 2)
        out["trailing_tp"] = tp
        cs = edf["chandelier_stop"].iloc[-1]
        exits = [x for x in [tp, (float(cs) if pd.notna(cs) else None)] if x is not None]
        out["effective_exit"] = round(max(exits), 2) if exits else None
        # 止盈分级(规范 8.4)
        tp_trig = tp is not None and close < tp
        sell = sig.get("sell_level")
        if tp_trig and sell in ("S2", "S3"):
            out["tp_level"] = "TP3"
        elif tp_trig:
            out["tp_level"] = "TP2"
        elif pnl is not None and pnl >= 30:
            out["tp_level"] = "TP1"

    out["action_for_you"], out["signal_type"] = _action(status, sig, out)
    return out


def _action(status, sig, out):
    """把信号翻译成"对你"的动作(规范 9.2/9.3)。返回 (action_for_you, signal_type)。"""
    buy, sell, tp = sig.get("buy_level"), sig.get("sell_level"), out.get("tp_level")
    pnl = out.get("your_pnl")
    if pnl is None:
        pnls = ""
    else:
        pnls = f"(浮盈+{pnl:.1f}%)" if pnl >= 0 else f"(浮亏{pnl:.1f}%)"

    if status == "EMPTY":
        if buy:
            return f"{buy} 买入信号,可考虑建仓", "BUY"
        if sell:
            return "出现卖出信号,空仓观望勿追", None
        return "无买入信号,观望", None

    if status == "HOLDING":
        if tp == "TP3" or sell == "S3":
            return f"{pnls}清仓或仅留底仓", ("SELL-TP" if tp else "SELL")
        if tp == "TP2" or sell == "S2":
            return f"{pnls}减仓 1/2", ("SELL-TP" if tp else "SELL")
        if tp == "TP1":
            return f"{pnls}止盈减仓 1/3", "SELL-TP"
        if sell == "S1":
            return f"{pnls}预警:上移止损、暂不卖", None
        if buy:
            return f"{pnls}趋势健康,可考虑加仓", "BUY"
        return f"{pnls}持有观察", None

    # WATCHING
    p = []
    if buy:
        p.append(f"{buy}买")
    if sell:
        p.append(f"{sell}卖")
    st = ("BUY" if buy else "SELL") if (buy or sell) else None
    return ("、".join(p) if p else "无信号"), st
