"""feifei_Ashare CLI(P0:数据底座冒烟)。

    py run.py fetch 600519
    py run.py fetch 688256 --source local_db
    py run.py fetch 600519 --source akshare      # 纯远端对照

后续阶段会加 score / signal / backtest / alert 子命令。
"""
import argparse
import datetime as dt
import sys

from smon import indicators, score, signals, sources
from smon.config import load_config
from smon.logsetup import get_logger, setup_logger


def _pad(s, width, right=False) -> str:
    """按显示宽度补齐(CJK 字符占 2 列),让含中文的表格列对齐。"""
    s = str(s)
    w = sum(2 if ord(ch) > 0x2E7F else 1 for ch in s)
    pad = " " * max(0, width - w)
    return (pad + s) if right else (s + pad)


def cmd_fetch(args) -> int:
    cfg = load_config(args.config)
    setup_logger(cfg.log_level, cfg.log_file)
    log = get_logger("cli")
    end = cfg.effective_end()
    src = args.source or cfg.data.source
    log.info(f"取数 {args.code}  源={src}  区间 [{cfg.start}..{end}]  topup={cfg.data.topup}")

    df = sources.fetch(args.code, cfg.start, end, source=src, cfg=cfg)
    if df.empty:
        log.error("无数据"); return 1

    last = df["date"].max().date().isoformat()
    today = dt.date.today().isoformat()
    print("\n=== 头部 2 行 ===")
    print(df.head(2).to_string(index=False))
    print("\n=== 尾部 5 行 ===")
    print(df.tail(5).to_string(index=False))
    print(f"\n行数: {len(df)}   区间: {df['date'].min().date()} .. {last}")
    print("各列缺失:", {c: int(df[c].isna().sum()) for c in df.columns})
    fresh = "是" if last >= today else f"否(最新 {last};若今天非交易日属正常)"
    print(f"数据是否到今天({today}): {fresh}")
    return 0


def _analyze(code, cfg, end):
    """取数→指标→打分→信号,返回单只票结果(数据不足返回 None)。"""
    df = sources.fetch(code, cfg.start, end, source=cfg.data.source, cfg=cfg)
    if df.empty or len(df) < 30:
        return None
    edf = indicators.enrich(df, cfg)
    last = edf.iloc[-1]
    st = cfg.stock(code)
    return {
        "code": str(code).split(".")[0].zfill(6),
        "name": (st.name if st else ""),
        "date": last["date"].date().isoformat(),
        "close": round(float(last["close"]), 2),
        "pct_chg": round(float(last["pct_chg"]), 2),
        "score": score.score_stock(edf, cfg),
        "sig": signals.evaluate(edf, cfg),
    }


def cmd_score(args) -> int:
    cfg = load_config(args.config)
    setup_logger(cfg.log_level, cfg.log_file)
    log = get_logger("cli")
    end = cfg.effective_end()
    codes = [args.code] if args.code else cfg.codes()
    rows = []
    for c in codes:
        r = _analyze(c, cfg, end)
        if r:
            rows.append(r)
        else:
            log.warning(f"{c}: 数据不足,跳过")
    if not rows:
        log.error("无可用结果"); return 1
    rows.sort(key=lambda x: x["score"]["total"], reverse=True)

    hdr = (_pad("代码", 8) + _pad("名称", 11) + _pad("现价", 9, True) + _pad("涨幅%", 8, True)
           + _pad("综合分", 8, True) + " " + _pad("档", 6) + _pad("趋势", 6, True)
           + _pad("量能", 6, True) + _pad("位置", 6, True) + "  信号")
    print("\n" + hdr)
    print("-" * 96)
    for r in rows:
        s, sig = r["score"], r["sig"]
        sigtxt = []
        if sig["buy_level"]:
            sigtxt.append(f"{sig['buy_level']}买[" + "/".join(sig["buy_rules"]) + "]")
        if sig["sell_level"]:
            sigtxt.append(f"{sig['sell_level']}卖[" + "/".join(sig["sell_rules"]) + "]")
        print(_pad(r["code"], 8) + _pad(r["name"], 11)
              + _pad(f"{r['close']:.2f}", 9, True) + _pad(f"{r['pct_chg']:+.2f}", 8, True)
              + _pad(f"{s['total']:+.1f}", 8, True) + " " + _pad(s["band"], 6)
              + _pad(f"{s['trend']:+.0f}", 6, True) + _pad(f"{s['volume']:+.0f}", 6, True)
              + _pad(f"{s['position']:+.0f}", 6, True) + "  "
              + (" | ".join(sigtxt) if sigtxt else "—"))
    print("\n打分档:强多>50 / 偏多20~50 / 中性±20 / 偏空-50~-20 / 强空<-50"
          "(P1 筹码分未计→正负略压缩;大盘/止盈/确认在 P3+)")
    return 0


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="config.yaml")
    p = argparse.ArgumentParser(prog="feifei", description="feifei_Ashare 自选股盘后监测",
                                parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", parents=[common], help="取单只股票前复权日线(冒烟)")
    f.add_argument("code")
    f.add_argument("--source", default="", help="local_db/tushare/akshare;空=用 config")
    f.set_defaults(func=cmd_fetch)
    sc = sub.add_parser("score", parents=[common], help="对自选股打分排序(+基础信号)")
    sc.add_argument("code", nargs="?", default="", help="指定单只;空=全部自选股")
    sc.set_defaults(func=cmd_score)
    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
