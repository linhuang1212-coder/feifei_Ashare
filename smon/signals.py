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


def evaluate(edf: pd.DataFrame, cfg, market_regime=None, chips=None, sector=None) -> dict:
    """对 enriched df 评估当前买卖信号(可传入大盘 regime、筹码 chips、板块 sector)。"""
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
    if chips and chips.get("peak_below") is not None:
        pb = chips["peak_below"]
        if pb > 0 and abs(close - pb) / pb < 0.03:           # 回踩下方筹码峰企稳(规范5.1)
            b1.append("回踩下方筹码峰")

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
    if chips and chips.get("high_peak_dispersing"):
        s1.append("高位筹码发散")
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
    if g("atr_stop_triggered") and not bool(prev.get("atr_stop_triggered")):  # 跌破当日(事件,非状态)
        s3.append("触及ATR吊灯止损")
    prior_low60 = edf["low"].iloc[-61:-1].min() if len(edf) > 61 else r["low"]
    if close < prior_low60:
        s3.append("跌破近60日前低(结构破坏)")
    if g("vol_surge") and broke_ll and close < prevclose and upsh > body * 2:
        s3.append("天量长上影+破生命线")
    if chips and chips.get("high_peak_dispersing") and broke_ll:
        s3.append("筹码派发+破生命线")

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

    # ===== 盈亏比闸门(规范 8.1,买入前置)=====
    rr = None
    if buy_level:
        cs = g("chandelier_stop")
        risk = (close - cs) if (cs is not None and pd.notna(cs)) else None
        prior_high60 = edf["high"].iloc[-61:-1].max() if len(edf) > 61 else r["high"]
        if not risk or risk <= 0:
            rr = 0.0
        else:
            resist = [x for x in [g("boll_up"), prior_high60] if pd.notna(x) and x > close]
            reward = (min(resist) - close) if resist else risk * 99.0   # 无上方压力=晴空,给足
            rr = round(reward / risk, 2)
        th = float((getattr(cfg, "take_profit", {}) or {}).get("min_risk_reward", 1.5))
        if rr < th:
            buy_level, buy_rules = None, []                # 盈亏比不足直接丢弃
        elif rr < 2:
            buy_level = {"B3": "B2", "B2": "B1", "B1": None}.get(buy_level, buy_level)

    # ===== 大盘环境过滤(规范 6.3,最高优先级)=====
    if market_regime == "NEUTRAL":
        buy_level = {"B3": "B2", "B2": "B1", "B1": None}.get(buy_level, buy_level)
    elif market_regime == "RISK_OFF":
        buy_level = None                                  # 大盘走弱暂停买入
        sell_level = {"S1": "S2", "S2": "S3", "S3": "S3"}.get(sell_level, sell_level)

    # ===== 板块强弱修正(规范6.3,叠在大盘之后)=====
    if sector:
        if sector.get("strong"):                          # 板块在风口:买入恢复一级
            buy_level = {"B1": "B2", "B2": "B3", "B3": "B3"}.get(buy_level, buy_level)
        elif sector.get("weak"):                          # 板块走弱:买入抑制 + 卖出强化
            buy_level = {"B3": "B2", "B2": "B1", "B1": None}.get(buy_level, buy_level)
            sell_level = {"S1": "S2", "S2": "S3", "S3": "S3"}.get(sell_level, sell_level)

    # ===== 主力/筹码动向(规范 5.4,中性提示,无主力资金数据→用筹码+量价近似)=====
    capital = None
    if chips:
        if chips.get("low_single_peak") and (g("vol_shrink") or g("vol_shrink_pullback")):
            capital = "疑似建仓"
        elif chips.get("high_peak_dispersing") and g("vol_stagnant"):
            capital = "疑似派发"
        elif g("vol_shrink_pullback") and pd.notna(ll) and abs(close - ll) / ll < 0.05:
            capital = "疑似洗盘"

    return {
        "buy_level": buy_level, "buy_rules": buy_rules,
        "sell_level": sell_level, "sell_rules": sell_rules,
        "pos_pctile": round(pp, 1), "risk_reward": rr,
        "weekly_trend": wt, "decision_mode": decision_mode,
        "market_regime": market_regime, "capital": capital, "sector": sector,
    }


# ============================================================ 指标达标清单
def feature_status(edf: pd.DataFrame, chips=None) -> list:
    """每只股的指标达标情况(规范 0.1:输出哪些条件满足),供人逐项核对。

    返回 [(分组名, [(项, 状态, 备注)])];状态为 bool(✓/✗)或字符串值。
    传入 chips 则附加筹码组。
    """
    r = edf.iloc[-1]

    def g(k):
        return r.get(k)

    close = r["close"]
    ll = g("lifeline")

    def above(x):
        return bool(pd.notna(x) and close > x)

    def fnum(k, fmt="{:.2f}"):
        v = g(k)
        return fmt.format(v) if pd.notna(v) else "—"

    pp = g("pos_pctile")
    wt_cn = {"WEEKLY_BULL": "多头", "WEEKLY_BEAR": "空头", "WEEKLY_NEUTRAL": "中性"}
    groups = [
        ("趋势", [
            ("多头排列(MA5>10>20>60)", bool(g("ma_bull_aligned")), ""),
            ("空头排列", bool(g("ma_bear_aligned")), ""),
            ("站上生命线", above(ll), f"生命线 {fnum('lifeline')}"),
            ("站上MA20", above(g("ma20")), f"MA20 {fnum('ma20')}"),
            ("站上MA60", above(g("ma60")), f"MA60 {fnum('ma60')}"),
            ("MACD零轴上方", bool(g("macd_above_zero")), f"DIF {fnum('dif')}"),
            ("MACD金叉(当日)", bool(g("macd_golden_cross")), ""),
            ("MACD死叉(当日)", bool(g("macd_dead_cross")), ""),
        ]),
        ("量能/换手", [
            ("量比(/5日均量)", fnum("volume_ratio"), ""),
            ("放量(>2×5日均量)", bool(g("vol_surge")), ""),
            ("巨量(>2.5×)", bool(g("vol_huge")), ""),
            ("温和放量(1.5~2×)", bool(g("vol_mild_surge")), ""),
            ("缩量(<0.7×)", bool(g("vol_shrink")), ""),
            ("价涨量增", bool(g("price_up_vol_up")), ""),
            ("放量滞涨(顶部嫌疑)", bool(g("vol_stagnant")), ""),
            ("换手率档位", str(g("turnover_tier")), f"{fnum('turnover','{:.2f}')}%"),
            ("换手骤升(>2.5×20日)", bool(g("turnover_spike")), ""),
        ]),
        ("位置", [
            ("120日价格分位", (f"{float(pp):.0f}%" if pd.notna(pp) else "—"), ""),
            ("近低位(<40%)", bool(pd.notna(pp) and float(pp) < 40), ""),
            ("近高位(>80%)", bool(pd.notna(pp) and float(pp) > 80), ""),
        ]),
        ("震荡(仅辅助)", [
            ("KDJ金叉", bool(g("kdj_golden_cross")),
             f"K{fnum('kdj_k','{:.0f}')}/D{fnum('kdj_d','{:.0f}')}/J{fnum('kdj_j','{:.0f}')}"),
            ("KDJ超卖", bool(g("kdj_oversold")), ""),
            ("KDJ超买", bool(g("kdj_overbought")), ""),
            ("RSI14>50", bool(g("rsi_above_50")), f"RSI {fnum('rsi14','{:.0f}')}"),
            ("底背离(MACD/RSI)", bool(g("macd_bottom_divergence") or g("rsi_bottom_divergence")), ""),
            ("顶背离(MACD/RSI)", bool(g("macd_top_divergence") or g("rsi_top_divergence")), ""),
            ("钝化失效(超买超卖失灵)", bool(g("daily_oscillator_failed")),
             "已切趋势/周线主导" if g("daily_oscillator_failed") else ""),
        ]),
        ("止损/通道", [
            ("ATR吊灯止损位", fnum("chandelier_stop"), "已触发⚠" if g("atr_stop_triggered") else ""),
            ("布林站上中轨", bool(g("boll_above_mid")), ""),
            ("布林收口(变盘前兆)", bool(g("boll_squeeze")), ""),
            ("放量突破上轨", bool(g("boll_break_upper")), ""),
        ]),
        ("周线背景(v2.1)", [
            ("周线趋势", wt_cn.get(g("weekly_trend"), "—"), f"周MA20 {fnum('w_ma20')}"),
        ]),
    ]
    if chips:
        def cf(v):
            return f"{v:.2f}" if isinstance(v, (int, float)) else "—"
        pr = chips.get("profit_ratio")
        groups.append(("筹码(估算)", [
            ("获利盘比例", (f"{pr * 100:.0f}%" if pr is not None else "—"),
             "获利盘>90%高位警惕" if chips.get("profit_ratio_high") else
             ("普遍套牢(底部特征)" if chips.get("profit_ratio_low") else "")),
            ("平均成本(cost50)", cf(chips.get("cost_50")), ""),
            ("90集中度", (f"{chips['concentration']:.2f}" if chips.get("concentration") is not None else "—"),
             "高度集中" if chips.get("concentrated") else ("发散" if chips.get("dispersed") else "")),
            ("低位单峰密集", bool(chips.get("low_single_peak")), ""),
            ("筹码上移(健康)", bool(chips.get("peak_upward_migration")), ""),
            ("高位筹码发散(派发嫌疑)", bool(chips.get("high_peak_dispersing")), ""),
            ("下方筹码峰(支撑)", cf(chips.get("peak_below")), ""),
            ("上方筹码峰(压力)", cf(chips.get("peak_above")), ""),
        ]))
    return groups
