"""数据源层 —— 只把最原始的前复权日线取回来,不做指标/信号(算/取分离)。

主源 local_db(只读 China_quant 的 daily_kline_v2:连续前复权日线,含换手率)。
本地库停在建库日时,用 akshare/tushare 补到 end,并在缝合点**按复权因子重锚对齐**,
消除两段 qfq 基准不同造成的跳变(规范 §2.4 缝合校验)。

统一输出列(STD_COLS):
    date, open, high, low, close, preclose, volume, amount, pct_chg, turnover
缺列填 NaN;preclose 缺失由 close 前移补。

铁律:只读 China_quant 库(mode=ro)绝不写;只 fork 代码绝不 import quant-pipeline。
daily_kline_v2 列名为正常 UTF-8 中文(日期/开盘/最高/最低/收盘/preclose/成交量/成交额/
换手率/涨跌幅/...),引号列名直接查,零编码处理(2026-06-27 实测确认)。
"""
import os
import sqlite3

import numpy as np
import pandas as pd

from .logsetup import get_logger

STD_COLS = ["date", "open", "high", "low", "close", "preclose",
            "volume", "amount", "pct_chg", "turnover"]


# ---------------------------------------------------------------- 代码格式
def to_tushare_code(code: str) -> str:
    """'600036'->'600036.SH';'000001'/'300xxx'->'.SZ';'4/8xxxxx'->'.BJ'。"""
    c = code6(code)
    if c.startswith("6"):
        ex = "SH"
    elif c.startswith(("0", "3")):
        ex = "SZ"
    elif c.startswith(("4", "8")):
        ex = "BJ"
    else:
        ex = "SH"
    return f"{c}.{ex}"


def code6(code: str) -> str:
    return str(code).split(".")[0].strip().zfill(6)


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=STD_COLS)


def _ensure_cols(df: pd.DataFrame) -> pd.DataFrame:
    """补齐标准列(缺填 NaN),preclose 缺失由 close 前移补,按标准顺序返回。"""
    if "preclose" not in df.columns or df["preclose"].isna().all():
        df = df.sort_values("date")
        df["preclose"] = df["close"].shift(1)
    for c in STD_COLS:
        if c not in df.columns:
            df[c] = np.nan
    return df[STD_COLS]


# ---------------------------------------------------------------- local_db
def _ro_conn(path: str) -> sqlite3.Connection:
    """只读连接,绝不污染 China_quant 生产库。"""
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _fetch_local_db(code, start, end, cfg) -> pd.DataFrame:
    path = cfg.data.local_db_path
    table = cfg.data.local_db_table
    log = get_logger("fetch")
    if not os.path.exists(path):
        log.warning(f"local_db 不存在: {path}")
        return _empty()
    sql = (
        f'SELECT "日期" AS date, "开盘" AS open, "最高" AS high, "最低" AS low, '
        f'"收盘" AS close, "preclose" AS preclose, "成交量" AS volume, '
        f'"成交额" AS amount, "涨跌幅" AS pct_chg, "换手率" AS turnover '
        f'FROM "{table}" WHERE code = ? AND "日期" BETWEEN ? AND ? ORDER BY "日期"'
    )
    con = _ro_conn(path)
    try:
        df = pd.read_sql(sql, con, params=[code6(code), start, end])
    except Exception as e:
        log.warning(f"local_db 读取失败({table}): {type(e).__name__}: {e}")
        return _empty()
    finally:
        con.close()
    if df.empty:
        return _empty()
    df["date"] = pd.to_datetime(df["date"])
    return _ensure_cols(df)


# ---------------------------------------------------------------- 远端补源
def _fetch_tushare(code, start, end, token) -> pd.DataFrame:
    import tushare as ts
    ts.set_token(token)
    df = ts.pro_bar(ts_code=to_tushare_code(code), adj="qfq", freq="D",
                    start_date=start.replace("-", ""), end_date=end.replace("-", ""))
    if df is None or df.empty:
        return _empty()
    df = df.rename(columns={"trade_date": "date", "vol": "volume", "pre_close": "preclose"})
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    # pro_bar 不返回换手率,留 NaN(缝合补的几天换手率缺;P1 若需可另查 daily_basic)
    return _ensure_cols(df)


def _fetch_akshare(code, start, end) -> pd.DataFrame:
    import akshare as ak
    df = ak.stock_zh_a_hist(symbol=code6(code), period="daily",
                            start_date=start.replace("-", ""),
                            end_date=end.replace("-", ""), adjust="qfq")
    if df is None or df.empty:
        return _empty()
    df = df.rename(columns={
        "日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
        "成交量": "volume", "成交额": "amount", "涨跌幅": "pct_chg", "换手率": "turnover",
    })
    df["date"] = pd.to_datetime(df["date"])
    return _ensure_cols(df)


def _fetch_remote(code, start, end, source, token) -> pd.DataFrame:
    src = (source or "akshare").lower()
    if src == "tushare":
        if not token:
            get_logger("fetch").warning(f"{code}: topup_source=tushare 但无 token,改用 akshare")
            return _fetch_akshare(code, start, end)
        return _fetch_tushare(code, start, end, token)
    return _fetch_akshare(code, start, end)


# ---------------------------------------------------------------- 缝合重锚
def _topup_local(df_local, code, end, cfg, token) -> pd.DataFrame:
    """local_db 停在建库日时,补到 end,并把**本地段**重锚到补源(今日前复权)基准。

    两段 qfq 基准不同(本地=建库日锚,补源=今日锚),无除权时仅差一个乘性常数。
    用重叠日(本地最后一日)算 ratio = 补源收盘 / 本地收盘,把本地段 OHLC×ratio 落到
    今日基准:这样「最新价 = 真实市价」(与券商一致)且缝合处无跳变;ratio 明显偏离 1
    → 缺口期内有除权(已被吸收)。成交量/成交额/换手率/涨跌幅与复权基准无关,不缩放。
    """
    log = get_logger("fetch")
    last = pd.to_datetime(df_local["date"]).max()
    if last >= pd.to_datetime(end):
        return df_local
    gap_start = last.strftime("%Y-%m-%d")            # 含重叠日,用于算重锚比
    src = _fetch_remote(code, gap_start, end, cfg.data.topup_source, token).sort_values("date")
    if src.empty:
        log.warning(f"{code}: topup 补源无数据 [{gap_start}..{end}]")
        return df_local
    last_close = float(df_local.loc[df_local["date"] == last, "close"].iloc[0])
    overlap = src[src["date"] == last]
    df = df_local.copy()
    if not overlap.empty and last_close > 0:
        ratio = float(overlap["close"].iloc[0]) / last_close        # 本地 → 今日基准
        if abs(ratio - 1.0) > 0.01:
            log.warning(f"{code}: 缝合 ratio={ratio:.4f} 偏离 1 → 缺口期有除权,"
                        f"已把本地段重锚到今日基准吸收跳变")
        if abs(ratio - 1.0) > 1e-9:
            for c in ("open", "high", "low", "close", "preclose"):
                df[c] = df[c] * ratio
    else:
        log.warning(f"{code}: 缝合无重叠日,未重锚(可能小跳变)")
    add = src[src["date"] > last]
    if add.empty:
        return df
    out = pd.concat([df, add[STD_COLS]], ignore_index=True)
    log.info(f"{code}: 本地 {len(df)} 行(已重锚)+ 补 {len(add)} 行 → {end},最新价=真实市价")
    return out


# ---------------------------------------------------------------- 统一入口
def fetch(code, start, end, source="local_db", cfg=None) -> pd.DataFrame:
    """拉单只前复权日线,返回标准列 DataFrame(未做指标)。"""
    log = get_logger("fetch")
    src = (source or "local_db").lower()
    token = ((cfg.tushare_token if cfg else "") or os.environ.get("TUSHARE_TOKEN", "")).strip()
    try:
        if src == "local_db":
            df = _fetch_local_db(code, start, end, cfg)
        elif src == "tushare":
            df = _fetch_tushare(code, start, end, token)
        elif src == "akshare":
            df = _fetch_akshare(code, start, end)
        else:
            raise ValueError(f"未知数据源: {source}(可选 local_db/tushare/akshare)")
    except Exception as e:
        log.error(f"{code} 拉取失败({src}): {type(e).__name__}: {e}")
        return _empty()

    if src == "local_db" and cfg is not None and getattr(cfg.data, "topup", False) and not df.empty:
        df = _topup_local(df, code, end, cfg, token)

    df = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    log.info(f"{src:<8} <- {code}  [{start}..{end}]  {len(df)} 行")
    return df
