"""事件驱动回测器(P1.5)—— 给后续每个信号/阈值提供"先验证再上"的地基。

为什么是事件驱动而非向量化(评审 §2.3):本系统信号带**持仓路径依赖**(空仓看买入、
持有看卖出),还要叠加次日确认/冷静期/regime 过滤,向量化算不对,必须逐 bar 回放。

无未来函数(硬保证):
  - `indicators.enrich` 已验证因果(第 t 行只用 ≤t 数据),故 `signals.evaluate(df[:i+1])`
    等价于"只用 ≤i 的信息"决策;
  - 决策在 bar i 收盘做出,成交在 **bar i+1 开盘**(决策绝不引用 i+1 的任何价)。

持仓机:单一满仓,空↔多。买入级别 ∈ entry_levels 入场,卖出级别 ∈ exit_levels 离场;
空仓时只看买、持有时只看卖 —— 这条天然化解了"同日 B 与 S 都触发"的冲突(评审 §2.1)。
成本由 costs.py 计入(买入价上浮、卖出价下浮)。
"""
import math

import pandas as pd

from . import costs, signals


def _max_drawdown(curve) -> float:
    peak, mdd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return mdd


def _summarize(trades, bench_ret, period) -> dict:
    rets = [t["ret"] for t in trades]
    out = {"n_trades": len(trades), "period": period, "benchmark_ret": bench_ret}
    if not rets:
        out.update(win_rate=None, avg_win=None, avg_loss=None, payoff=None,
                   profit_factor=None, total_ret=0.0, max_drawdown=0.0, avg_hold=None)
        return out
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    eq, curve = 1.0, [1.0]
    for r in rets:
        eq *= (1 + r)
        curve.append(eq)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    out.update(
        win_rate=len(wins) / len(rets),
        avg_win=avg_win, avg_loss=avg_loss,
        payoff=(avg_win / abs(avg_loss)) if avg_loss != 0 else math.inf,
        profit_factor=(sum(wins) / abs(sum(losses))) if (losses and sum(losses) != 0) else math.inf,
        total_ret=eq - 1,
        max_drawdown=_max_drawdown(curve),
        avg_hold=sum(t["hold_days"] for t in trades) / len(trades),
    )
    return out


def run(edf: pd.DataFrame, cfg) -> dict:
    bt = cfg.backtest or {}
    entry_levels = set(bt.get("entry_levels", ["B2", "B3"]))
    exit_levels = set(bt.get("exit_levels", ["S2", "S3"]))
    cm = costs.from_cfg(cfg)
    df = edf.sort_values("date").reset_index(drop=True)
    n = len(df)

    def _ready(i):
        r = df.iloc[i]
        return (pd.notna(r.get("ma60")) and pd.notna(r.get("atr"))
                and pd.notna(r.get("pos_pctile")))

    start_i = next((i for i in range(n) if _ready(i)), None)
    if start_i is None or start_i >= n - 1:
        return {"trades": [], "metrics": _summarize([], None, ("", "")), "insufficient": True}

    anchored = bool(bt.get("position_anchored_stop", True))
    atr_n = float((cfg.thresholds or {}).get("atr_n", 3.0))
    highs, closes, atrs = df["high"].values, df["close"].values, df["atr"].values

    pos = 0
    e_px = e_date = e_reason = e_idx = peak = None
    trades = []
    for i in range(start_i, n - 1):                 # 到 n-1,保证 i+1 存在
        sig = signals.evaluate(df.iloc[:i + 1], cfg)
        nxt = df.iloc[i + 1]
        nxt_open, nxt_date = float(nxt["open"]), nxt["date"]
        if pos == 0 and sig["buy_level"] in entry_levels:
            e_px = nxt_open * (1 + cm.buy_rate())
            e_date, e_idx = nxt_date, i + 1
            e_reason = f"{sig['buy_level']}:" + "/".join(sig["buy_rules"])
            pos, peak = 1, e_px
        elif pos == 1:
            peak = max(peak, float(highs[i]))       # 持仓期最高价
            sell = sig["sell_level"]
            # 持仓锚定吊灯止损:从持仓最高回落 N×ATR
            stop_breach = (anchored and pd.notna(atrs[i])
                           and closes[i] < peak - atr_n * float(atrs[i]))
            # 趋势类离场(锚定模式下排除"仅全局ATR-S3",由持仓锚定接管)
            only_atr = (sell == "S3" and sig["sell_rules"] == ["触及ATR吊灯止损"])
            trend_exit = (sell in exit_levels) and not (anchored and only_atr)
            if stop_breach or trend_exit:
                x_px = nxt_open * (1 - cm.sell_rate())
                reason = (f"{sell}:" + "/".join(sig["sell_rules"])) if trend_exit else "持仓ATR止损"
                trades.append(dict(
                    entry_date=e_date.date().isoformat(), exit_date=nxt_date.date().isoformat(),
                    entry_px=round(e_px, 2), exit_px=round(x_px, 2),
                    ret=x_px / e_px - 1, hold_days=int(i + 1 - e_idx),
                    entry_reason=e_reason, exit_reason=reason))
                pos, peak = 0, None

    if pos == 1:                                    # 期末未平仓 → 末日收盘 MTM
        last = df.iloc[-1]
        x_px = float(last["close"]) * (1 - cm.sell_rate())
        trades.append(dict(
            entry_date=e_date.date().isoformat(), exit_date=last["date"].date().isoformat(),
            entry_px=round(e_px, 2), exit_px=round(x_px, 2),
            ret=x_px / e_px - 1, hold_days=int(n - 1 - e_idx),
            entry_reason=e_reason, exit_reason="期末未平仓(MTM)", open_position=True))

    bench_ret = float(df.iloc[-1]["close"]) / float(df.iloc[start_i]["close"]) - 1
    period = (df.iloc[start_i]["date"].date().isoformat(),
              df.iloc[-1]["date"].date().isoformat())
    result = {"trades": trades, "metrics": _summarize(trades, bench_ret, period),
              "insufficient": False}

    oos = bt.get("oos_split", "")
    if oos:
        ins = [t for t in trades if t["exit_date"] < oos]
        oss = [t for t in trades if t["exit_date"] >= oos]
        result["metrics_is"] = _summarize(ins, None, (period[0], oos))
        result["metrics_oos"] = _summarize(oss, None, (oos, period[1]))
    return result
