"""feifei_Ashare CLI(P0:数据底座冒烟)。

    py run.py fetch 600519
    py run.py fetch 688256 --source local_db
    py run.py fetch 600519 --source akshare      # 纯远端对照

后续阶段会加 score / signal / backtest / alert 子命令。
"""
import argparse
import datetime as dt
import sys

from smon import sources
from smon.config import load_config
from smon.logsetup import get_logger, setup_logger


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
    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
