"""信号确认与失败处理(规范第10章)。过滤假信号 + 持久化信号历史。

- 次日确认(10.1):信号需持续到次日才"确认"。本实现用**数据派生**(今日 raw 信号 vs 昨日
  raw 信号),信号当日新现=PENDING、已持续=CONFIRMED;require_next_day_confirm 关则恒 CONFIRMED。
- 信号历史 + 去重(10.3):每日信号写 feifei.db 的 signal_log,(run_date,code,kind) 主键幂等去重。
- 止损冷静期(9.3):最近 cooldown 个交易日内出现过 S3-ATR 止损 → 抑制新买入。
- 失败计数/卖出后收复(10.2)留待后续(需更长 run 累积)。
"""
import os
import sqlite3

from . import signals as _sig
from .logsetup import get_logger


def _db(cfg) -> str:
    return getattr(cfg, "db_path", "") or ""


def init(cfg):
    db = _db(cfg)
    if not db:
        return
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE IF NOT EXISTS signal_log("
                "run_date TEXT, code TEXT, kind TEXT, level TEXT, price REAL, "
                "confirm TEXT, note TEXT, PRIMARY KEY(run_date, code, kind))")
    con.commit()
    con.close()


def confirm_status(edf, cfg) -> str | None:
    """次日确认:今日 raw 信号较昨日是否持续。无信号返回 None。"""
    if not (getattr(cfg, "confirmation", {}) or {}).get("require_next_day_confirm", True):
        return "CONFIRMED"
    if len(edf) < 3:
        return "PENDING"
    today = _sig.evaluate(edf, cfg)
    if not (today.get("buy_level") or today.get("sell_level")):
        return None
    prev = _sig.evaluate(edf.iloc[:-1], cfg)
    if today.get("buy_level"):
        return "CONFIRMED" if prev.get("buy_level") else "PENDING"
    return "CONFIRMED" if prev.get("sell_level") else "PENDING"


def _trading_dates(cfg):
    """缓存里的全部交易日(升序),用于按交易日算冷静期。"""
    db = _db(cfg)
    if not db or not os.path.exists(db):
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        ds = [r[0] for r in con.execute("SELECT DISTINCT date FROM kline_cache ORDER BY date")]
        con.close()
        return ds
    except Exception:
        return []


def cooldown_active(cfg, code, run_date) -> bool:
    """止损冷静期(规范9.3):最近 cooldown_days 个交易日内有 S3-ATR 止损 → True。"""
    db = _db(cfg)
    if not db or not os.path.exists(db):
        return False
    days = int((getattr(cfg, "confirmation", {}) or {}).get("cooldown_days_after_stop", 3))
    c6 = str(code).split(".")[0].zfill(6)
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT run_date FROM signal_log WHERE code=? AND kind='SELL' AND note LIKE '%ATR%' "
            "ORDER BY run_date DESC LIMIT 3", (c6,)).fetchall()
        con.close()
    except Exception:
        return False
    if not rows:
        return False
    dates = _trading_dates(cfg)
    if run_date not in dates:
        return False
    ri = dates.index(run_date)
    for (sd,) in rows:
        if sd in dates and 0 <= ri - dates.index(sd) <= days:
            return True
    return False


def log_signals(cfg, results, run_date) -> int:
    """把当日有信号的票写入 signal_log(幂等去重)。results=[{code,sig,close,...}]。"""
    init(cfg)
    db = _db(cfg)
    if not db:
        return 0
    con = sqlite3.connect(db)
    n = 0
    for r in results:
        sig = r.get("sig") or {}
        code = r["code"]
        price = r.get("close")
        if sig.get("buy_level"):
            con.execute("INSERT OR REPLACE INTO signal_log VALUES(?,?,?,?,?,?,?)",
                        (run_date, code, "BUY", sig["buy_level"], price,
                         r.get("confirm"), "/".join(sig.get("buy_rules", []))))
            n += 1
        if sig.get("sell_level"):
            con.execute("INSERT OR REPLACE INTO signal_log VALUES(?,?,?,?,?,?,?)",
                        (run_date, code, "SELL", sig["sell_level"], price,
                         r.get("confirm"), "/".join(sig.get("sell_rules", []))))
            n += 1
    con.commit()
    con.close()
    return n


def history(cfg, code, limit=20):
    """读某只票的信号历史(供回看)。"""
    db = _db(cfg)
    if not db or not os.path.exists(db):
        return []
    c6 = str(code).split(".")[0].zfill(6)
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT run_date,kind,level,price,confirm,note FROM signal_log WHERE code=? "
            "ORDER BY run_date DESC LIMIT ?", (c6, limit)).fetchall()
        con.close()
        return rows
    except Exception:
        return []
