"""多周期:周线趋势背景(规范 v2.1 第 7 章)。常驻、每日更新、因果合成。

核心实现坑(评审 §4.2.1):周线必须**因果合成**——历史第 t 日的周线背景只能用
"本周截至 t 的不完整 K 线 + 此前已完结的完整周",**严禁** resample 出"完整本周"
(那是未来函数,回测会虚高)。本模块按日逐行推进,把周线 MA/MACD 对齐回日线索引:
  - 完整周:其最后一个交易日收盘 = 该周 weekly close,只在周切换时"提交";
  - 当前在进行周:用当日收盘作"临时本周收盘"参与计算(每天滚动更新)。
只有定方向的 MA/MACD 上周线(§7.5);KDJ 周线此处不做主角。
weekly_trend 三档:WEEKLY_BULL / WEEKLY_BEAR / WEEKLY_NEUTRAL。
"""
import numpy as np
import pandas as pd


def _ema_step(prev, x, span):
    a = 2.0 / (span + 1)
    return x if prev is None else a * x + (1 - a) * prev


def add_weekly(df: pd.DataFrame, cfg) -> pd.DataFrame:
    mp = getattr(cfg, "multi_period", {}) or {}
    ma_list = list(mp.get("weekly_ma", [5, 10, 20, 30]))
    df = df.sort_values("date").reset_index(drop=True).copy()
    n = len(df)
    close = df["close"].values
    iso = df["date"].dt.isocalendar()
    wkid = (iso["year"].astype(int) * 100 + iso["week"].astype(int)).values

    completed = []                      # 已完结周的 weekly close(按序)
    ema12c = ema26c = deac = None       # 仅基于已完结周"提交"的 EMA 状态
    w_ma = {m: np.full(n, np.nan) for m in ma_list}
    w_dif = np.full(n, np.nan)
    w_dea = np.full(n, np.nan)
    prev = None

    for t in range(n):
        wk = wkid[t]
        if prev is None:
            prev = wk
        elif wk != prev:                # 上一周在 t-1 完结 → 提交其 weekly close
            fc = close[t - 1]
            completed.append(fc)
            ema12c = _ema_step(ema12c, fc, 12)
            ema26c = _ema_step(ema26c, fc, 26)
            deac = _ema_step(deac, ema12c - ema26c, 9)
            prev = wk
        # 当前在进行周:用当日收盘作临时本周收盘(因果,只用 ≤t)
        e12 = _ema_step(ema12c, close[t], 12)
        e26 = _ema_step(ema26c, close[t], 26)
        dif_p = e12 - e26
        w_dif[t] = dif_p
        w_dea[t] = _ema_step(deac, dif_p, 9)
        for m in ma_list:
            if len(completed) >= m - 1:           # 需满 m 个周点(含本周)才算
                recent = completed[-(m - 1):] if m > 1 else []
                w_ma[m][t] = (sum(recent) + close[t]) / m

    for m in ma_list:
        df[f"w_ma{m}"] = w_ma[m]
    df["w_dif"] = w_dif
    df["w_dea"] = w_dea

    # weekly_trend(规范 7.2)
    ma20 = df["w_ma20"].values if "w_ma20" in df.columns else np.full(n, np.nan)
    trend = []
    for t in range(n):
        m20 = ma20[t]
        if np.isnan(m20):
            trend.append("WEEKLY_NEUTRAL")
        elif close[t] > m20 and (w_dif[t] > 0 or w_dif[t] > w_dea[t]):
            trend.append("WEEKLY_BULL")
        elif close[t] < m20 and w_dif[t] < w_dea[t]:
            trend.append("WEEKLY_BEAR")
        else:
            trend.append("WEEKLY_NEUTRAL")
    df["weekly_trend"] = trend
    return df
