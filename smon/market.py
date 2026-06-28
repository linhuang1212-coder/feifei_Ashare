"""大盘环境过滤(规范第6章,v2.0 最高优先级)。

约80%个股涨跌由大盘决定。每日先算大盘 regime(三档),作为全局上下文传入个股信号与
打分,对买卖信号二次过滤(规范0.7 / 6.3)。本地库无上证/创业板指数,用 akshare 取。

market_regime 三档:RISK_ON(进攻)/ NEUTRAL(中性)/ RISK_OFF(防守)。
多指数合成:任一 RISK_OFF → 整体 RISK_OFF;全部 RISK_ON → RISK_ON;否则 NEUTRAL(从严)。
"""
import os
import sqlite3

import numpy as np
import pandas as pd

from .logsetup import get_logger

_CACHE = {}
_SECTOR_CACHE = {}


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


# ============================================================ 板块强弱(规范6.2,申万一级)
def get_sectors(cfg) -> dict | None:
    """各申万一级行业近20日(等权成员)涨幅 → 全市场排名分位。带缓存。

    返回 {members: {code:行业名}, name2pct: {行业名:0-100分位}, sect_ret: {行业名:20日涨幅}}。
    需 feifei 缓存(kline_cache)+ China_quant 的 sw_l1_member。
    """
    cache = getattr(cfg, "db_path", "") or ""
    src = cfg.data.local_db_path
    if not (cache and os.path.exists(cache) and os.path.exists(src)):
        return None
    try:
        con = sqlite3.connect(f"file:{cache}?mode=ro", uri=True)
        dates = [r[0] for r in con.execute(
            "SELECT DISTINCT date FROM kline_cache ORDER BY date DESC LIMIT 21")]
        if len(dates) < 21:
            con.close(); return None
        key = dates[0]
        if key in _SECTOR_CACHE:
            con.close(); return _SECTOR_CACHE[key]
        d_now, d_20 = dates[0], dates[20]
        rows = con.execute("SELECT code, date, close FROM kline_cache WHERE date IN (?,?)",
                           (d_now, d_20)).fetchall()
        con.close()
        cs = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        members = {str(c).zfill(6): n for c, n in cs.execute(
            "SELECT code, l1_name FROM sw_l1_member WHERE out_date IS NULL")}
        cs.close()
    except Exception as e:
        get_logger("market").warning(f"板块取数失败: {type(e).__name__}: {e}")
        return None
    px = {}
    for code, date, close in rows:
        px.setdefault(code, {})[date] = close
    by_sect = {}
    for code, dd in px.items():
        name = members.get(str(code).zfill(6))
        if name and d_now in dd and d_20 in dd and dd[d_20]:
            by_sect.setdefault(name, []).append(dd[d_now] / dd[d_20] - 1)
    sect_ret = {n: sum(v) / len(v) for n, v in by_sect.items() if v}
    if not sect_ret:
        return None
    ranked = sorted(sect_ret.items(), key=lambda x: x[1])
    m = len(ranked)
    name2pct = {n: round((i + 1) / m * 100, 1) for i, (n, _) in enumerate(ranked)}
    out = {"members": members, "name2pct": name2pct, "sect_ret": sect_ret}
    _SECTOR_CACHE[dates[0]] = out
    return out


def sector_info(cfg, code) -> dict | None:
    """个股所属申万行业的强弱(规范6.2):pct>70 强、<30 弱。"""
    s = get_sectors(cfg)
    if not s:
        return None
    name = s["members"].get(str(code).split(".")[0].zfill(6))
    if not name or name not in s["name2pct"]:
        return None
    pct = s["name2pct"][name]
    return {"name": name, "pct": pct, "strong": pct > 70, "weak": pct < 30,
            "ret20": round(s["sect_ret"].get(name, 0) * 100, 1)}
