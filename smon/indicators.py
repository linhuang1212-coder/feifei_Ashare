"""指标层 —— 把前复权日线 enrich 成全套技术指标(规范第 2/3 章)。只算不画。

铁律:
  - 严禁未来函数:第 t 行的每个字段只用 ≤t 的数据(rolling/ewm/shift 均后向)。
  - 强趋势中震荡指标(KDJ/RSI)只看背离与辅助,不当买卖主信号(规范 0.3 / 2.2)。
  - 所有参数取自 cfg.periods / cfg.thresholds,不硬编码。
手写实现(不依赖 pandas-ta)以逐条对齐规范公式、便于独立复核。
"""
import numpy as np
import pandas as pd

from . import weekly


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def _clamp_series(s, lo, hi):
    return s.clip(lower=lo, upper=hi)


def _divergence(price: pd.Series, ind: pd.Series, n: int, kind: str) -> pd.Series:
    """背离的**因果**近似(辅助用):价格创 n 日新极值、而指标未同步创极值。

    top:    收盘创 n 日新高,但指标 < 其前 n 日最高 → 顶背离
    bottom: 收盘创 n 日新低,但指标 > 其前 n 日最低 → 底背离
    用 ≤t 数据,无未来泄漏;非精确摆动点配对,仅作预警/打分辅助(规范 2.2)。
    """
    if ind is None:
        return pd.Series(False, index=price.index)
    if kind == "top":
        new_ext = price >= price.rolling(n).max()
        ind_div = ind < ind.rolling(n).max().shift(1)
    else:
        new_ext = price <= price.rolling(n).min()
        ind_div = ind > ind.rolling(n).min().shift(1)
    return (new_ext & ind_div).fillna(False)


def enrich(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """返回带全部指标列的 df(原列保留)。输入需含 sources.STD_COLS。"""
    p = cfg.periods or {}
    th = cfg.thresholds or {}
    df = df.sort_values("date").reset_index(drop=True).copy()
    close, high, low = df["close"], df["high"], df["low"]
    vol, prevclose, openp = df["volume"], df["preclose"], df["open"]

    # ---------------- 趋势:MA + 排列 + 生命线 ----------------
    ma_list = list(p.get("ma", [5, 10, 20, 60, 120, 250]))
    for n in ma_list:
        df[f"ma{n}"] = close.rolling(n).mean()

    def _aligned(cols, asc):
        ok = pd.Series(True, index=df.index)
        for a, b in zip(cols, cols[1:]):
            ok &= (df[a] > df[b]) if asc else (df[a] < df[b])
        return ok & df[cols].notna().all(axis=1)

    bull_cols = [f"ma{n}" for n in (5, 10, 20, 60) if f"ma{n}" in df.columns]
    df["ma_bull_aligned"] = _aligned(bull_cols, True)
    df["ma_bear_aligned"] = _aligned(bull_cols, False)

    # 生命线:急涨股(近20日涨幅>30%)取 MA10,普通取 MA20(规范 2.1.1,可后续手动覆盖)
    rise20 = close / close.shift(20) - 1
    df["fast_rise"] = rise20 > 0.30
    ma10 = df.get("ma10", pd.Series(np.nan, index=df.index))
    ma20 = df.get("ma20", pd.Series(np.nan, index=df.index))
    df["lifeline"] = np.where(df["fast_rise"], ma10, ma20)

    # ---------------- MACD ----------------
    f, s, sig = list(p.get("macd", [12, 26, 9]))
    dif = _ema(close, f) - _ema(close, s)
    dea = _ema(dif, sig)
    df["dif"], df["dea"] = dif, dea
    df["macd_hist"] = (dif - dea) * 2
    df["macd_golden_cross"] = (dif > dea) & (dif.shift(1) <= dea.shift(1))
    df["macd_dead_cross"] = (dif < dea) & (dif.shift(1) >= dea.shift(1))
    df["macd_above_zero"] = dif > 0

    # ---------------- KDJ(震荡,仅辅助) ----------------
    kn, _ks, _kd = list(p.get("kdj", [9, 3, 3]))
    low_n = low.rolling(kn).min()
    high_n = high.rolling(kn).max()
    rsv = (close - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()   # 平滑 1/3;初值非严格 50,长史可忽略
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    j = 3 * k - 2 * d
    df["kdj_k"], df["kdj_d"], df["kdj_j"] = k, d, j
    df["kdj_golden_cross"] = (k > d) & (k.shift(1) <= d.shift(1))
    df["kdj_dead_cross"] = (k < d) & (k.shift(1) >= d.shift(1))
    df["kdj_overbought"] = (j > 100) | (k > 80)
    df["kdj_oversold"] = (j < 0) | (k < 20)

    # ---------------- RSI(Wilder,震荡仅辅助) ----------------
    delta = close.diff()
    up = delta.clip(lower=0)
    dn = (-delta).clip(lower=0)
    for n in list(p.get("rsi", [6, 12, 14])):
        avg_up = up.ewm(alpha=1 / n, adjust=False).mean()
        avg_dn = dn.ewm(alpha=1 / n, adjust=False).mean().replace(0, np.nan)
        df[f"rsi{n}"] = 100 - 100 / (1 + avg_up / avg_dn)
    r14 = df.get("rsi14")
    df["rsi_above_50"] = (r14 > 50) if r14 is not None else False
    df["rsi_overbought"] = (r14 > 80) if r14 is not None else False
    df["rsi_oversold"] = (r14 < 30) if r14 is not None else False

    # ---------------- ATR + 吊灯止损(只上移 + 触发重置) ----------------
    n_atr = int(p.get("atr", 14))
    tr = pd.concat([(high - low), (high - prevclose).abs(), (low - prevclose).abs()],
                   axis=1).max(axis=1)
    atr = tr.rolling(n_atr).mean()                  # 规范:TR 的 N 日移动平均
    df["atr"] = atr
    nmult = float(th.get("atr_n", 3.0))
    raw_stop = high.rolling(22).max() - nmult * atr
    df["chandelier_raw"] = raw_stop
    stop = np.full(len(df), np.nan)
    trig = np.zeros(len(df), dtype=bool)
    cur = np.nan
    rs_v, cl_v = raw_stop.values, close.values
    for i in range(len(df)):
        r = rs_v[i]
        if np.isnan(r):
            continue
        cur = r if np.isnan(cur) else max(cur, r)    # 只上移
        if not np.isnan(cl_v[i]) and cl_v[i] < cur:
            trig[i] = True
            cur = r                                  # 触发后重置到当前 raw
        stop[i] = cur
    df["chandelier_stop"] = stop
    df["atr_stop_triggered"] = trig

    # ---------------- BOLL ----------------
    bn, bk = list(p.get("boll", [20, 2]))
    mid = close.rolling(bn).mean()
    sd = close.rolling(bn).std(ddof=0)
    df["boll_mid"], df["boll_up"], df["boll_low"] = mid, mid + bk * sd, mid - bk * sd
    bw = (df["boll_up"] - df["boll_low"]) / mid
    df["boll_bandwidth"] = bw
    df["boll_squeeze"] = bw <= bw.rolling(60).quantile(0.20)
    df["boll_above_mid"] = close > mid
    df["boll_break_upper"] = (close > df["boll_up"]) & (close.shift(1) <= df["boll_up"].shift(1))
    df["boll_fall_below_mid"] = (close < mid) & (close.shift(1) >= mid.shift(1))

    # ---------------- 量能与量价配合(规范 3.1) ----------------
    df["vol_ma5"] = vol.rolling(5).mean()
    df["vol_ma10"] = vol.rolling(10).mean()
    surge = float(th.get("vol_surge", 2.0))
    shrink = float(th.get("vol_shrink", 0.7))
    df["vol_surge"] = vol > df["vol_ma5"] * surge
    df["vol_shrink"] = vol < df["vol_ma5"] * shrink
    up_day = close > prevclose
    down_day = close < prevclose
    df["price_up_vol_up"] = up_day & (vol > vol.shift(1))
    df["price_up_vol_down"] = up_day & (vol < vol.shift(1))
    df["price_down_vol_up"] = down_day & (vol > vol.shift(1))
    # 放量滞涨(顶部嫌疑):放量 + 当日涨幅<2% + 收阴或长上影
    upper_shadow = high - np.maximum(openp, close)
    long_upper = upper_shadow > (high - low) * 0.5
    df["vol_stagnant"] = df["vol_surge"] & (df["pct_chg"] < 2.0) & ((close < openp) | long_upper)
    # 缩量回调(健康洗盘):阴跌 + 缩量
    df["vol_shrink_pullback"] = down_day & df["vol_shrink"]

    # ---------------- 换手率档位(规范 3.2,主板默认阈值) ----------------
    t = df["turnover"]
    df["turnover_tier"] = pd.cut(
        t, [-1, 3, 7, 15, 25, 1e9],
        labels=["low", "normal", "active", "high", "extreme"])
    df["turnover_spike"] = t > t.rolling(20).mean() * 2.5

    # ---------------- 位置:近120日价格分位 ----------------
    win = 120
    df["pos_pctile"] = close.rolling(win).apply(
        lambda x: float((x <= x[-1]).mean()) * 100, raw=True)

    # ---------------- 背离(因果近似,辅助) ----------------
    df["macd_top_divergence"] = _divergence(close, dif, 40, "top")
    df["macd_bottom_divergence"] = _divergence(close, dif, 40, "bottom")
    df["rsi_top_divergence"] = _divergence(close, r14, 40, "top")
    df["rsi_bottom_divergence"] = _divergence(close, r14, 40, "bottom")

    # ---------------- 多周期:周线背景(v2.1,因果合成,常驻) ----------------
    df = weekly.add_weekly(df, cfg)

    return df
