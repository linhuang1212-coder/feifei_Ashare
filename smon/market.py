"""大盘环境过滤(规范第6章,v2.0 最高优先级)。

约80%个股涨跌由大盘决定。每日先算大盘 regime(三档),作为全局上下文传入个股信号与
打分,对买卖信号二次过滤(规范0.7 / 6.3)。本地库无上证/创业板指数,用 akshare 取。

market_regime 三档:RISK_ON(进攻)/ NEUTRAL(中性)/ RISK_OFF(防守)。
多指数合成:任一 RISK_OFF → 整体 RISK_OFF;全部 RISK_ON → RISK_ON;否则 NEUTRAL(从严)。
"""
import numpy as np
import pandas as pd

from .logsetup import get_logger

_CACHE = {}


def _akshare_symbol(code: str) -> str:
    c = str(code).split(".")[0].zfill(6)
    return ("sh" if c.startswith("000") else "sz" if c.startswith("399") else "sh") + c


def _fetch_index(code, start, end) -> pd.DataFrame:
    log = get_logger("market")
    sym = _akshare_symbol(code)
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol=sym)
        if df is None or df.empty:
            return pd.DataFrame()
        df["date"] = pd.to_datetime(df["date"])
        df = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))]
        return df.sort_values("date").reset_index(drop=True)
    except Exception as e:
        log.warning(f"指数 {sym} 取数失败: {type(e).__name__}: {e}")
        return pd.DataFrame()


def _ema(s, span):
    return s.ewm(span=span, adjust=False).mean()


def _index_state(df: pd.DataFrame) -> dict:
    """单指数状态 + 三档 regime(规范 6.1)。"""
    close = df["close"]
    ma5, ma10, ma20 = close.rolling(5).mean(), close.rolling(10).mean(), close.rolling(20).mean()
    dif = _ema(close, 12) - _ema(close, 26)
    dea = _ema(dif, 9)
    i = -1
    above20 = bool(close.iloc[i] > ma20.iloc[i])
    ma_bull = bool(ma5.iloc[i] > ma10.iloc[i] > ma20.iloc[i])
    ma_bear = bool(ma5.iloc[i] < ma10.iloc[i] < ma20.iloc[i])
    macd_dead = bool(dif.iloc[i] < dea.iloc[i] and dif.iloc[i - 1] >= dea.iloc[i - 1] and dif.iloc[i] > 0)
    chg = float(close.iloc[i] / close.iloc[i - 1] - 1)
    vol_panic = False
    if "volume" in df.columns:
        vma5 = df["volume"].rolling(5).mean()
        vol_panic = bool(chg < -0.01 and df["volume"].iloc[i] > vma5.iloc[i] * 1.2)
    if above20 and ma_bull:
        regime = "RISK_ON"
    elif (not above20) or ma_bear or vol_panic:
        regime = "RISK_OFF"
    else:
        regime = "NEUTRAL"
    return {"regime": regime, "above_ma20": above20, "ma_bull": ma_bull,
            "ma_bear": ma_bear, "macd_dead": macd_dead, "vol_panic": vol_panic,
            "pct_chg": round(chg * 100, 2), "close": round(float(close.iloc[i]), 2)}


def get_regime(cfg, end: str) -> dict:
    """计算大盘 regime(全局,每日一次,带缓存)。返回 {regime, indices:{code:state}}。"""
    codes = (cfg.market_filter or {}).get("index_codes", ["000001", "399006"])
    key = (tuple(codes), end, cfg.start)
    if key in _CACHE:
        return _CACHE[key]
    log = get_logger("market")
    states = {}
    for c in codes:
        df = _fetch_index(c, cfg.start, end)
        if df.empty or len(df) < 30:
            log.warning(f"指数 {c} 数据不足,跳过")
            continue
        states[str(c)] = _index_state(df)
    if not states:
        out = {"regime": "NEUTRAL", "indices": {}, "note": "无指数数据,降级中性"}
        _CACHE[key] = out
        return out
    regs = [s["regime"] for s in states.values()]
    if "RISK_OFF" in regs:
        overall = "RISK_OFF"
    elif all(r == "RISK_ON" for r in regs):
        overall = "RISK_ON"
    else:
        overall = "NEUTRAL"
    out = {"regime": overall, "indices": states}
    log.info(f"大盘 regime={overall}  " + " ".join(
        f"{c}:{s['regime']}({s['pct_chg']:+.2f}%)" for c, s in states.items()))
    _CACHE[key] = out
    return out


REGIME_CN = {"RISK_ON": "进攻", "NEUTRAL": "中性", "RISK_OFF": "防守"}
