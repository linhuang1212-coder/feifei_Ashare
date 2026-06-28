"""信号判定(规范第 5 章)—— 买入 B1/B2/B3、卖出 S1/S2/S3,多指标共振、分级输出。

P1 范围:仅实现**不依赖筹码/大盘/止盈/确认**的条件(那些在 P3/P4/P5 接入)。
故 capital(筹码动向)与部分含筹码峰的条件此处略过,后续阶段补齐。
共振原则(规范 0.1):买卖结论由"趋势+量能+(位置/动能)"多类共同确认,不靠单一指标。

返回每只票当前达到的**最高级**买入信号与卖出信号及其触发理由。冲突时的主信号路由
(持仓×大盘)在 P3/P5 统一,本阶段两侧都返回、不强判谁压谁。
"""
import pandas as pd


def _tier(v) -> str:
    return str(v) if v is not None and pd.notna(v) else ""


def evaluate(edf: pd.DataFrame, cfg) -> dict:
    """对 enriched df 评估当前买卖信号。"""
    r = edf.iloc[-1]
    prev = edf.iloc[-2] if len(edf) > 1 else r

    def g(k):
        return r.get(k)

    close, prevclose = r["close"], r["preclose"]
    ll = g("lifeline")
    pp = float(g("pos_pctile")) if pd.notna(g("pos_pctile")) else 50.0
    high_pos, low_pos = pp >= 80, pp <= 40
    tier = _tier(g("turnover_tier"))
    upsh = r["high"] - max(r["open"], close)
    body = abs(close - r["open"])
    rng = max(r["high"] - r["low"], 1e-9)

    # ================= 买入 BUY =================
    # B1 关注级:任意 ≥2 条
    b1 = []
    if g("macd_bottom_divergence"):
        b1.append("MACD底背离")
    if g("kdj_oversold") and g("kdj_golden_cross"):
        b1.append("超卖区KDJ金叉")
    if g("rsi_bottom_divergence"):
        b1.append("RSI底背离")
    if (pd.notna(prev.get("rsi14")) and pd.notna(g("rsi14"))
            and prev["rsi14"] < 30 <= g("rsi14")):
        b1.append("RSI回升出超卖")
    if low_pos and tier in ("active", "high", "extreme") and g("vol_surge"):
        b1.append("低位放量活跃")

    # B2 建仓级:趋势+量能+位置 各 ≥1
    trend_ok = ((g("macd_golden_cross") and g("macd_above_zero"))
                or g("ma_bull_aligned") or (pd.notna(ll) and close > ll))
    vol_ok = g("price_up_vol_up") or (g("vol_surge") and close > prevclose)
    pos_ok = low_pos
    b2 = trend_ok and vol_ok and pos_ok

    # B3 强烈级:多头排列 + 放量突破
    prior_high20 = edf["high"].iloc[-21:-1].max() if len(edf) > 21 else r["high"]
    breakout = g("boll_break_upper") or (close >= prior_high20)
    b3 = bool(g("ma_bull_aligned") and g("vol_surge") and breakout)

    buy_level, buy_rules = None, []
    if b3:
        buy_level = "B3"
        buy_rules = ["多头排列", "放量突破" + ("布林上轨" if g("boll_break_upper") else "近20日高")]
    elif b2:
        buy_level = "B2"
        buy_rules = [x for x, ok in [("趋势转好", trend_ok), ("量能配合", vol_ok),
                                     ("位置不高", pos_ok)] if ok]
    elif len(b1) >= 2:
        buy_level = "B1"
        buy_rules = b1

    # ================= 卖出 SELL =================
    broke_ll = pd.notna(ll) and close < ll
    prev_ll = prev.get("lifeline")
    crossed_down_ll = broke_ll and (pd.isna(prev_ll) or prev["close"] >= prev_ll)

    # S1 黄色预警:任意 ≥1
    s1 = []
    if g("macd_top_divergence") or g("rsi_top_divergence"):
        s1.append("顶背离")
    if g("vol_stagnant"):
        s1.append("放量滞涨")
    if g("turnover_spike") and high_pos:
        s1.append("高位换手骤升")
    if high_pos and upsh > body * 2 and upsh > rng * 0.5:
        s1.append("高位长上影")

    # S2 橙色:任意 ≥1 → 减仓
    s2 = []
    if crossed_down_ll and g("vol_surge"):
        s2.append("放量跌破生命线")
    if g("macd_dead_cross") and g("macd_above_zero"):
        s2.append("零轴上方MACD死叉")
    if g("boll_fall_below_mid"):
        s2.append("跌回布林中轨下方")
    if ((g("macd_top_divergence") or g("rsi_top_divergence"))
            and close < prevclose and g("vol_surge")):
        s2.append("顶背离+放量下跌")
    if high_pos and tier in ("high", "extreme") and g("vol_stagnant"):
        s2.append("高位极端换手+放量滞涨")

    # S3 红色:任意 ≥1 → 清仓/留底仓
    s3 = []
    if g("atr_stop_triggered"):
        s3.append("触及ATR吊灯止损")
    prior_low60 = edf["low"].iloc[-61:-1].min() if len(edf) > 61 else r["low"]
    if close < prior_low60:
        s3.append("跌破近60日前低(结构破坏)")
    if g("vol_surge") and broke_ll and close < prevclose and upsh > body * 2:
        s3.append("天量长上影+破生命线")

    # ===== v2.1 周线背景 + 钝化趋势主导(P3.5,规范第7章 7.4) =====
    mp = getattr(cfg, "multi_period", {}) or {}
    wt = r.get("weekly_trend", "WEEKLY_NEUTRAL")
    decision_mode = "NORMAL"
    if mp.get("enable"):
        if wt == "WEEKLY_BEAR":
            # 中期方向向下:清仓级 + 买入逆势降一级(7.4 / 7.4背景过滤表)
            decision_mode = "TREND_LED"
            s3 = s3 + ["周线转空(中期方向向下)"]
            buy_level = {"B3": "B2", "B2": "B1", "B1": None}.get(buy_level, buy_level)
            if buy_level:
                buy_rules = buy_rules + ["⚠逆周线大势已降级"]
        elif (mp.get("weekly_gate_exits") and wt == "WEEKLY_BULL"
              and pd.notna(ll) and close > ll):
            # 周线多头且站上生命线:日线 ATR 止损此刻是噪音,忽略(7.4:超买是强势,继续持有)
            decision_mode = "TREND_LED"
            s3 = [x for x in s3 if "ATR" not in x]

    sell_level, sell_rules = None, []
    if s3:
        sell_level, sell_rules = "S3", s3
    elif s2:
        sell_level, sell_rules = "S2", s2
    elif s1:
        sell_level, sell_rules = "S1", s1

    return {
        "buy_level": buy_level, "buy_rules": buy_rules,
        "sell_level": sell_level, "sell_rules": sell_rules,
        "pos_pctile": round(pp, 1),
        "weekly_trend": wt, "decision_mode": decision_mode,
    }
