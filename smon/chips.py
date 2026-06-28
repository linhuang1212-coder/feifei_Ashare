"""筹码分布(筹码峰)估算(规范第4章)。数据源不直接提供,按"三角分布+换手衰减"估算。

算法(规范4.1,因果——只用 ≤当日 数据):
  1. 取近 window 日;
  2. 每日成交筹码在 [low, high] 内按三角分布(典型价处密度最高);
  3. 换手衰减:新分布 = 旧分布×(1−当日换手率) + 当日新增(归一)×当日换手率;
  4. 迭代累加 → 当前筹码分布直方图(价位→占比,和为1)。

⚠️ 筹码分布为**估算值**,与真实持仓有偏差;主力对倒会虚增量→污染估算。仅作支撑压力/
获利盘的上下文参考,绝不单独触发买卖(规范4 / 0.3)。不进回测(估算+开销大),仅供
盘后/实时显示与打分筹码桶。
"""
import numpy as np
import pandas as pd


def _distribution(sub: pd.DataFrame, bins: int):
    """对一段日线算筹码分布,返回(价格档中心, 归一权重)。"""
    lo = float(sub["low"].min())
    hi = float(sub["high"].max())
    if not (hi > lo):
        return np.array([lo]), np.array([1.0])
    edges = np.linspace(lo, hi, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2
    dist = np.zeros(bins)
    low = sub["low"].values
    high = sub["high"].values
    close = sub["close"].values
    turn = sub["turnover"].values
    for i in range(len(sub)):
        l, h, c = low[i], high[i], close[i]
        shape = np.zeros(bins)
        if not (h > l) or np.isnan(h) or np.isnan(l):
            idx = min(max(int(np.searchsorted(edges, c)) - 1, 0), bins - 1)
            shape[idx] = 1.0
        else:
            p = (h + l + c) / 3.0                       # 典型价,密度峰
            mask = (centers >= l) & (centers <= h)
            x = centers[mask]
            tri = np.where(x <= p, (x - l) / max(p - l, 1e-9), (h - x) / max(h - p, 1e-9))
            shape[mask] = np.clip(tri, 0, None)
            s = shape.sum()
            if s > 0:
                shape /= s
            else:
                idx = min(max(int(np.searchsorted(edges, c)) - 1, 0), bins - 1)
                shape[idx] = 1.0
        t = turn[i] / 100.0
        t = 0.05 if np.isnan(t) else min(max(t, 0.0), 1.0)
        dist = shape.copy() if dist.sum() == 0 else dist * (1 - t) + shape * t
    s = dist.sum()
    if s > 0:
        dist /= s
    return centers, dist


def _cum_price(centers, dist, q):
    cum = np.cumsum(dist)
    idx = int(np.searchsorted(cum, q))
    return float(centers[min(idx, len(centers) - 1)])


def _concentration(centers, dist):
    c50 = _cum_price(centers, dist, 0.5)
    c90l = _cum_price(centers, dist, 0.05)
    c90h = _cum_price(centers, dist, 0.95)
    conc = (c90h - c90l) / c50 if c50 > 0 else float("nan")
    return c50, c90l, c90h, conc


def compute(df: pd.DataFrame, cfg) -> dict | None:
    """算当前筹码分布 + 衍生字段 + 筹码信号(规范4.2/4.3)。数据不足返回 None。"""
    th = cfg.thresholds or {}
    ch = getattr(cfg, "chips", {}) or {}
    window = int(ch.get("window", 120))
    bins = int(ch.get("bins", 200))
    df = df.sort_values("date").reset_index(drop=True)
    if len(df) < 30:
        return None

    sub = df.tail(window)
    centers, dist = _distribution(sub, bins)
    close = float(df["close"].iloc[-1])
    c50, c90l, c90h, conc = _concentration(centers, dist)
    profit = float(dist[centers < close].sum())          # 获利盘比例(当前价之下)

    below, above = centers < close, centers > close
    peak_below = float(centers[below][np.argmax(dist[below])]) if below.any() and dist[below].max() > 0 else None
    peak_above = float(centers[above][np.argmax(dist[above])]) if above.any() and dist[above].max() > 0 else None

    win = min(120, len(df))
    pos = float((df["close"].tail(win) <= close).mean() * 100)

    # 20日前的集中度/成本中位,用于发散与上移判定(各再算一次分布)
    conc_prev = c50_prev = None
    if len(df) > window + 20:
        cp, dp = _distribution(df.iloc[-(window + 20):-20], bins)
        c50_prev, _, _, conc_prev = _concentration(cp, dp)

    concentrated = bool(pd.notna(conc) and conc < float(th.get("chip_concentrated", 0.15)))
    dispersed = bool(pd.notna(conc) and conc > 0.30)
    pr_high = bool(profit > float(th.get("profit_ratio_high", 0.90)))
    pr_low = bool(profit < 0.10)
    low_single_peak = bool(pos < 40 and concentrated)
    dispersing = bool(conc_prev is not None and pd.notna(conc) and conc > conc_prev)
    high_peak_dispersing = bool(pos > 80 and dispersing and pr_high)
    upward_migration = bool(c50_prev is not None and c50 > c50_prev and not high_peak_dispersing)

    return {
        "centers": centers, "weights": dist,
        "cost_50": round(c50, 2), "cost_90_low": round(c90l, 2), "cost_90_high": round(c90h, 2),
        "concentration": round(conc, 3) if pd.notna(conc) else None,
        "profit_ratio": round(profit, 3),
        "peak_below": round(peak_below, 2) if peak_below else None,
        "peak_above": round(peak_above, 2) if peak_above else None,
        "pos": round(pos, 0),
        "concentrated": concentrated, "dispersed": dispersed,
        "profit_ratio_high": pr_high, "profit_ratio_low": pr_low,
        "single_peak_dense": concentrated,
        "low_single_peak": low_single_peak,
        "high_peak_dispersing": high_peak_dispersing,
        "peak_upward_migration": upward_migration,
    }
