"""feifei_Ashare CLI(P0:数据底座冒烟)。

    py run.py fetch 600519
    py run.py fetch 688256 --source local_db
    py run.py fetch 600519 --source akshare      # 纯远端对照

后续阶段会加 score / signal / backtest / alert 子命令。
"""
import argparse
import datetime as dt
import math
import sys

from smon import backtest, chips, indicators, market, score, signals, sources
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


def _analyze(code, cfg, end, regime=None):
    """取数→指标→打分→信号,返回单只票结果(数据不足返回 None)。"""
    df = sources.fetch(code, cfg.start, end, source=cfg.data.source, cfg=cfg)
    if df.empty or len(df) < 30:
        return None
    edf = indicators.enrich(df, cfg)
    ch = chips.compute(edf, cfg)
    last = edf.iloc[-1]
    st = cfg.stock(code)
    return {
        "code": str(code).split(".")[0].zfill(6),
        "name": (st.name if st else ""),
        "date": last["date"].date().isoformat(),
        "close": round(float(last["close"]), 2),
        "pct_chg": round(float(last["pct_chg"]), 2),
        "score": score.score_stock(edf, cfg, market_regime=regime, chips=ch),
        "sig": signals.evaluate(edf, cfg, market_regime=regime, chips=ch),
    }


def cmd_score(args) -> int:
    cfg = load_config(args.config)
    setup_logger(cfg.log_level, cfg.log_file)
    log = get_logger("cli")
    end = cfg.effective_end()
    reg = market.get_regime(cfg, end)
    regime = reg["regime"]
    print(f"\n大盘环境:{regime}({market.REGIME_CN.get(regime,'')})  " + "  ".join(
        f"{c} {s['regime']}({s['pct_chg']:+.2f}%)" for c, s in reg.get("indices", {}).items()))
    codes = [args.code] if args.code else cfg.codes()
    rows = []
    for c in codes:
        r = _analyze(c, cfg, end, regime=regime)
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
            rr = sig.get("risk_reward")
            rrs = f" 盈亏比{rr:.1f}" if rr is not None else ""
            sigtxt.append(f"{sig['buy_level']}买[" + "/".join(sig["buy_rules"]) + f"]{rrs}")
        if sig["sell_level"]:
            sigtxt.append(f"{sig['sell_level']}卖[" + "/".join(sig["sell_rules"]) + "]")
        print(_pad(r["code"], 8) + _pad(r["name"], 11)
              + _pad(f"{r['close']:.2f}", 9, True) + _pad(f"{r['pct_chg']:+.2f}", 8, True)
              + _pad(f"{s['total']:+.1f}", 8, True) + " " + _pad(s["band"], 6)
              + _pad(f"{s['trend']:+.0f}", 6, True) + _pad(f"{s['volume']:+.0f}", 6, True)
              + _pad(f"{s['position']:+.0f}", 6, True) + "  "
              + (" | ".join(sigtxt) if sigtxt else "—"))
    print("\n打分档:强多>50 / 偏多20~50 / 中性±20 / 偏空-50~-20 / 强空<-50"
          "(筹码桶已接[估算]、大盘环境已修正、盈亏比闸门已接;持仓/信号确认在 P5)")
    return 0


def _pct(x):
    return "—" if x is None else f"{x * 100:+.1f}%"


def _num(x):
    if x is None:
        return "—"
    return "∞" if x == math.inf else f"{x:.2f}"


def _print_bt(tag, m):
    if not m or m["n_trades"] == 0:
        print(f"  {tag:<6}: 无交易"); return
    bench = f" | 基准 {_pct(m['benchmark_ret'])}" if m.get("benchmark_ret") is not None else ""
    print(f"  {tag:<6}: 交易 {m['n_trades']:>2} | 胜率 {m['win_rate'] * 100:>3.0f}% | "
          f"盈亏比 {_num(m['payoff'])} | 盈利因子 {_num(m['profit_factor'])} | "
          f"总收益 {_pct(m['total_ret'])} | 回撤 {_pct(m['max_drawdown'])} | "
          f"均持 {m['avg_hold']:.0f}天{bench}")


def cmd_backtest(args) -> int:
    cfg = load_config(args.config)
    setup_logger(cfg.log_level, cfg.log_file)
    log = get_logger("cli")
    end = cfg.effective_end()
    codes = [args.code] if args.code else cfg.codes()
    for c in codes:
        df = sources.fetch(c, cfg.start, end, source=cfg.data.source, cfg=cfg)
        if df.empty or len(df) < 150:
            log.warning(f"{c}: 数据不足(<150),跳过"); continue
        edf = indicators.enrich(df, cfg)
        res = backtest.run(edf, cfg)
        st = cfg.stock(c)
        m, per = res["metrics"], res["metrics"]["period"]
        bt = cfg.backtest or {}
        print(f"\n=== {c} {st.name if st else ''} 回测 [{per[0]}..{per[1]}] "
              f"入场{bt.get('entry_levels', ['B2', 'B3'])} 出场{bt.get('exit_levels', ['S2', 'S3'])} ===")
        _print_bt("全样本", m)
        if "metrics_is" in res:
            _print_bt("样本内", res["metrics_is"])
            _print_bt("样本外", res["metrics_oos"])
        ts = res["trades"]
        if ts and args.trades > 0:
            print(f"  近 {min(args.trades, len(ts))} 笔:")
            for t in ts[-args.trades:]:
                flag = " [期末未平]" if t.get("open_position") else ""
                print(f"    {t['entry_date']} → {t['exit_date']}  {t['ret'] * 100:+5.1f}%  "
                      f"持{t['hold_days']:>2}天  入[{t['entry_reason']}] 出[{t['exit_reason']}]{flag}")
    return 0


def cmd_check(args) -> int:
    """打印每只股的指标达标清单(自己逐项过一遍)。"""
    cfg = load_config(args.config)
    setup_logger(cfg.log_level, cfg.log_file)
    log = get_logger("cli")
    end = cfg.effective_end()
    codes = [args.code] if args.code else cfg.codes()
    for c in codes:
        df = sources.fetch(c, cfg.start, end, source=cfg.data.source, cfg=cfg)
        if df.empty or len(df) < 30:
            log.warning(f"{c}: 数据不足,跳过"); continue
        edf = indicators.enrich(df, cfg)
        ch = chips.compute(edf, cfg)
        last = edf.iloc[-1]
        st = cfg.stock(c)
        print("\n" + "=" * 60)
        print(f"{c} {st.name if st else ''}  现价 {float(last['close']):.2f}  "
              f"{float(last['pct_chg']):+.2f}%  ({last['date'].date()})")
        print("=" * 60)
        for gname, items in signals.feature_status(edf, chips=ch):
            print(f"【{gname}】")
            for name, status, note in items:
                if isinstance(status, bool):
                    line = f"  {'✓' if status else '·'} {name}"
                    if note:
                        line += f"  — {note}"
                else:
                    line = f"  ▸ {name}: {status}"
                    if note:
                        line += f"  ({note})"
                print(line)
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
    bt = sub.add_parser("backtest", parents=[common], help="事件驱动回测信号策略")
    bt.add_argument("code", nargs="?", default="", help="指定单只;空=全部自选股")
    bt.add_argument("--trades", type=int, default=5, help="打印最近 N 笔交易(0=不打印)")
    bt.set_defaults(func=cmd_backtest)
    ck = sub.add_parser("check", parents=[common], help="打印个股指标达标清单(逐项核对)")
    ck.add_argument("code", nargs="?", default="", help="指定单只;空=全部自选股")
    ck.set_defaults(func=cmd_check)
    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
