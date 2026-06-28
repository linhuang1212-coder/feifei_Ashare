"""综合打分 −100..+100(规范 10.2)。趋势35% + 量能25% + 位置20% + 筹码20%。

P1 说明:筹码分尚未实现(P4 补),此处 chip=0 → 正负分被压缩约 20%,属预期;
KDJ/RSI 仅作 ±5 微调,且超买在多头排列下不扣分(规范 0.3 / 10.2)。
环境(大盘/板块)调整在 P3 接入,本阶段不修正。
"""
import pandas as pd


def _clamp(x, lo=-100.0, hi=100.0):
    return max(lo, min(hi, x))


def _band(total):
    if total > 50:
        return "强多"
    if total >= 20:
        return "偏多"
    if total > -20:
        return "中性"
    if total > -50:
        return "偏空"
    return "强空"


def score_stock(edf: pd.DataFrame, cfg, market_regime=None) -> dict:
    """对 enriched df 的最新一行打分。返回 total/band/四桶分/理由。

    market_regime 传入时做整体修正(规范 10.2 v2.0):NEUTRAL 正分×0.8;
    RISK_OFF 正分×0.5、负分×1.2(放大风险)。
    """
    r = edf.iloc[-1]

    def g(k):
        return r.get(k)

    # ---- 趋势分 ----
    trend, tr = 0.0, []
    if g("ma_bull_aligned"):
        trend += 30; tr.append("+30 多头排列")
    if g("ma_bear_aligned"):
        trend -= 30; tr.append("-30 空头排列")
    ll = g("lifeline")
    if pd.notna(ll):
        if r["close"] > ll:
            trend += 20; tr.append("+20 站上生命线")
        else:
            trend -= 30; tr.append("-30 跌破生命线")
    if g("macd_golden_cross"):
        trend += 20; tr.append("+20 MACD金叉")
    if g("macd_dead_cross"):
        trend -= 20; tr.append("-20 MACD死叉")
    if g("macd_above_zero"):
        trend += 10; tr.append("+10 MACD零轴上")
    if g("macd_top_divergence") or g("rsi_top_divergence"):
        trend -= 25; tr.append("-25 顶背离")
    if g("macd_bottom_divergence") or g("rsi_bottom_divergence"):
        trend += 25; tr.append("+25 底背离")
    trend = _clamp(trend)

    # ---- 量能分 ----
    vol, vr = 0.0, []
    if g("price_up_vol_up"):
        vol += 25; vr.append("+25 价涨量增")
    if g("vol_surge") and r["close"] > r["preclose"]:
        vol += 20; vr.append("+20 放量上涨")
    if g("price_up_vol_down"):
        vol -= 15; vr.append("-15 价涨量缩")
    if g("vol_stagnant"):
        vol -= 25; vr.append("-25 放量滞涨")
    if g("vol_shrink_pullback"):
        vol += 10; vr.append("+10 缩量回调(健康洗盘)")
    if g("price_down_vol_up"):
        vol -= 20; vr.append("-20 价跌量增")
    vol = _clamp(vol)

    # ---- 位置分(近120日分位:越低越高,0%→+40 ... 100%→-40 线性) ----
    pos, pr = 0.0, []
    pp = g("pos_pctile")
    if pd.notna(pp):
        pos = 40 - 0.8 * float(pp)
        pr.append(f"{pos:+.0f} 位置分位{float(pp):.0f}%")
    pos = _clamp(pos)

    # ---- 筹码分(P4) ----
    chip = 0.0

    # ---- KDJ/RSI 微调 ±5 ----
    adj, ar = 0.0, []
    if g("rsi_above_50"):
        adj += 5; ar.append("+5 RSI>50")
    if (g("kdj_overbought") or g("rsi_overbought")) and not g("ma_bull_aligned"):
        adj -= 5; ar.append("-5 超买(非多头)")

    w = cfg.scoring_weights or {}
    total = (trend * w.get("trend", 0.35) + vol * w.get("volume", 0.25)
             + pos * w.get("position", 0.20) + chip * w.get("chip", 0.20) + adj)
    # 大盘环境整体修正(规范 10.2 v2.0)
    if market_regime == "NEUTRAL":
        if total > 0:
            total *= 0.8
    elif market_regime == "RISK_OFF":
        total *= 0.5 if total > 0 else 1.2
    total = _clamp(total)

    return {
        "total": round(total, 1), "band": _band(total),
        "trend": round(trend, 1), "volume": round(vol, 1),
        "position": round(pos, 1), "chip": chip, "adj": adj,
        "market_regime": market_regime,
        "reasons": {"trend": tr, "volume": vr, "position": pr, "adj": ar},
    }
