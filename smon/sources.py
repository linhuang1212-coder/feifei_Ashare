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
from pathlib import Path

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


# ---------------------------------------------------------------- 全市场缓存(code+date 索引)
def _cache_path(cfg) -> str:
    return getattr(cfg, "db_path", "") or ""


def cache_ready(cfg) -> bool:
    """feifei 缓存库存在且含 kline_cache 表。"""
    p = _cache_path(cfg)
    if not p or not os.path.exists(p):
        return False
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        ok = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='kline_cache'").fetchone()
        con.close()
        return ok is not None
    except Exception:
        return False


def cache_max_date(cfg) -> str:
    """缓存最新日期(供显示数据新鲜度)。"""
    if not cache_ready(cfg):
        return "—"
    try:
        con = sqlite3.connect(f"file:{_cache_path(cfg)}?mode=ro", uri=True)
        d = con.execute("SELECT MAX(date) FROM kline_cache").fetchone()[0]
        con.close()
        return d or "—"
    except Exception:
        return "—"


def _fetch_cache(code, start, end, cfg) -> pd.DataFrame | None:
    """从 feifei 缓存(code+date 索引)秒读单只;无缓存返回 None。"""
    if not cache_ready(cfg):
        return None
    try:
        con = sqlite3.connect(f"file:{_cache_path(cfg)}?mode=ro", uri=True)
        df = pd.read_sql(
            "SELECT date,open,high,low,close,preclose,volume,amount,pct_chg,turnover "
            "FROM kline_cache WHERE code=? AND date BETWEEN ? AND ? ORDER BY date",
            con, params=[code6(code), start, end])
        con.close()
    except Exception as e:
        get_logger("fetch").warning(f"缓存读取失败: {type(e).__name__}: {e}")
        return None
    if df.empty:
        return _empty()
    df["date"] = pd.to_datetime(df["date"])
    return _ensure_cols(df)


def build_cache(cfg, chunksize: int = 300000) -> int:
    """把 China_quant daily_kline_v2 [cfg.start..建库日] 批量读进 feifei 缓存库,
    加 (code,date) 索引,使全市场按 code 取数从慢扫变秒读。只读源库,绝不写。"""
    log = get_logger("cache")
    src = cfg.data.local_db_path
    cache = _cache_path(cfg)
    if not os.path.exists(src):
        log.error(f"源库不存在: {src}"); return 0
    Path(cache).parent.mkdir(parents=True, exist_ok=True)
    out = sqlite3.connect(cache)
    out.execute("DROP TABLE IF EXISTS kline_cache")
    out.execute("CREATE TABLE kline_cache(code TEXT, date TEXT, open REAL, high REAL, "
                "low REAL, close REAL, preclose REAL, volume REAL, amount REAL, "
                "pct_chg REAL, turnover REAL)")
    rocon = _ro_conn(src)
    q = (f'SELECT code, "日期" date, "开盘" open, "最高" high, "最低" low, "收盘" close, '
         f'"preclose" preclose, "成交量" volume, "成交额" amount, "涨跌幅" pct_chg, '
         f'"换手率" turnover FROM "{cfg.data.local_db_table}" WHERE "日期" >= ?')
    total = 0
    for chunk in pd.read_sql(q, rocon, params=[cfg.start], chunksize=chunksize):
        chunk.to_sql("kline_cache", out, if_exists="append", index=False)
        total += len(chunk)
        log.info(f"缓存写入 {total} 行…")
    rocon.close()
    log.info("建索引 (code,date)…")
    out.execute("CREATE INDEX idx_cache_code_date ON kline_cache(code, date)")
    out.commit()
    out.close()
    log.info(f"缓存完成:{total} 行 → {cache}")
    return total


def update_cache(cfg, log=None) -> int:
    """全市场批量补鲜:用 tushare 按交易日一次取全市场(daily+adj_factor+daily_basic),
    算前复权(锚定最后交易日=真实价),把已有缓存的除权股历史重锚后,追加缺口日。

    qfq 锚定 last_day:gap 日 qfq = 不复权 × adj_factor[d] / adj_factor[last];
    已有缓存(建库日基准,06-05收盘=真实)重锚到 last 基准:×k,k = adj_factor[06-05]/adj_factor[last]。
    单元换算:tushare amount 千元→×1000 元;vol 手、turnover_rate %、pct_chg % 同库口径。
    """
    log = log or get_logger("cache")
    cache = _cache_path(cfg)
    if not cache_ready(cfg):
        log.error("无缓存,请先 build_cache"); return 0
    token = (cfg.tushare_token or os.environ.get("TUSHARE_TOKEN", "")).strip()
    if not token:
        log.error("无 tushare token,无法批量补鲜"); return 0
    import tushare as ts
    ts.set_token(token)
    pro = ts.pro_api()

    con = sqlite3.connect(cache)
    cmax = con.execute("SELECT MAX(date) FROM kline_cache").fetchone()[0]
    end = cfg.effective_end()
    if cmax >= end:
        log.info(f"缓存已到 {cmax},无需补鲜"); con.close(); return 0
    cmax_c, end_c = cmax.replace("-", ""), end.replace("-", "")
    cal = pro.trade_cal(exchange="SSE", start_date=cmax_c, end_date=end_c, is_open="1")
    gap = sorted(d for d in cal["cal_date"].astype(str).tolist() if d > cmax_c)
    if not gap:
        log.info("无新交易日"); con.close(); return 0
    last_day = gap[-1]
    log.info(f"批量补鲜 {cmax} → {last_day}({len(gap)} 个交易日)…")

    afL = pro.adj_factor(trade_date=last_day).set_index("ts_code")["adj_factor"]
    af0 = pro.adj_factor(trade_date=cmax_c).set_index("ts_code")["adj_factor"]
    # 1) 重锚已有缓存(建库日基准→last_day基准):除权股 ×k
    common = af0.index.intersection(afL.index)
    k = (af0[common] / afL[common]).replace([np.inf, -np.inf], np.nan).dropna()
    div = k[(k - 1).abs() > 1e-6]
    if len(div):
        kmap = pd.DataFrame({"code": [c.split(".")[0].zfill(6) for c in div.index],
                             "k": div.values}).groupby("code")["k"].first().reset_index()
        con.execute("DROP TABLE IF EXISTS _div_k")
        con.execute("CREATE TEMP TABLE _div_k(code TEXT PRIMARY KEY, k REAL)")
        con.executemany("INSERT OR REPLACE INTO _div_k VALUES(?,?)",
                        list(kmap.itertuples(index=False, name=None)))
        con.execute("UPDATE kline_cache SET "
                    "open=open*(SELECT k FROM _div_k d WHERE d.code=kline_cache.code), "
                    "high=high*(SELECT k FROM _div_k d WHERE d.code=kline_cache.code), "
                    "low=low*(SELECT k FROM _div_k d WHERE d.code=kline_cache.code), "
                    "close=close*(SELECT k FROM _div_k d WHERE d.code=kline_cache.code), "
                    "preclose=preclose*(SELECT k FROM _div_k d WHERE d.code=kline_cache.code) "
                    "WHERE code IN (SELECT code FROM _div_k)")
        con.commit()
        log.info(f"重锚 {len(kmap)} 只除权股历史缓存(到 last_day 基准)")

    # 2) 取缺口日 → qfq(last_day基准)→ 追加
    total = 0
    for d in gap:
        daily = pro.daily(trade_date=d)
        if daily is None or daily.empty:
            continue
        adj = pro.adj_factor(trade_date=d).set_index("ts_code")["adj_factor"]
        basic = pro.daily_basic(trade_date=d, fields="ts_code,turnover_rate").set_index(
            "ts_code")["turnover_rate"]
        m = daily.set_index("ts_code")
        fac = (adj.reindex(m.index) / afL.reindex(m.index)).fillna(1.0).values
        out = pd.DataFrame({
            "code": [c.split(".")[0].zfill(6) for c in m.index],
            "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
            "open": m["open"].values * fac, "high": m["high"].values * fac,
            "low": m["low"].values * fac, "close": m["close"].values * fac,
            "preclose": m["pre_close"].values * fac,
            "volume": m["vol"].values, "amount": m["amount"].values * 1000.0,
            "pct_chg": m["pct_chg"].values, "turnover": basic.reindex(m.index).values,
        })
        out.to_sql("kline_cache", con, if_exists="append", index=False)
        total += len(out)
        log.info(f"  补 {out['date'].iloc[0]}: {len(out)} 只")
    con.commit()
    con.close()
    log.info(f"补鲜完成:新增 {total} 行,缓存到 {last_day}")
    return total


def list_universe(cfg) -> list:
    """全市场代码(来自缓存,按用户要求**不过滤** ST/北交所/次新)。无缓存退回自选。"""
    if not cache_ready(cfg):
        return cfg.codes()
    con = sqlite3.connect(f"file:{_cache_path(cfg)}?mode=ro", uri=True)
    codes = [r[0] for r in con.execute("SELECT DISTINCT code FROM kline_cache ORDER BY code")]
    con.close()
    return codes


def _fetch_local_db(code, start, end, cfg) -> pd.DataFrame:
    log = get_logger("fetch")
    # 优先 feifei 缓存(code+date 索引,秒读);无则直读 daily_kline_v2(按 code 慢扫)
    cdf = _fetch_cache(code, start, end, cfg)
    if cdf is not None and not cdf.empty:
        return cdf
    path = cfg.data.local_db_path
    table = cfg.data.local_db_table
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
    """补源:tushare(有 token)优先,失败/空则回退 akshare(免 token),保证稳健。"""
    src = (source or "akshare").lower()
    if src == "tushare" and token:
        try:
            df = _fetch_tushare(code, start, end, token)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            get_logger("fetch").warning(f"{code}: tushare 补失败转 akshare: {type(e).__name__}: {e}")
    return _fetch_akshare(code, start, end)


# ---------------------------------------------------------------- 股票名(China_quant stock_list)
_NAME_CACHE = None


def load_names(cfg) -> dict:
    global _NAME_CACHE
    if _NAME_CACHE is not None:
        return _NAME_CACHE
    names = {}
    path = cfg.data.local_db_path
    if os.path.exists(path):
        try:
            con = _ro_conn(path)
            names = {str(c).split(".")[0].zfill(6): n
                     for c, n in con.execute("SELECT code, name FROM stock_list").fetchall()}
            con.close()
        except Exception:
            names = {}
    _NAME_CACHE = names
    return names


def name_of(code, cfg) -> str:
    return load_names(cfg).get(code6(code), "")


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
