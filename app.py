"""feifei_Ashare 仪表盘(规范 输出三)—— 可视盯盘面。

两视图:① 自选股总览(打分排序 + 信号 + 周线背景);② 单股详情卡(趋势/量能/位置/
震荡/周线/信号 + K线图)。定位为监测/提醒,给人决策用,非自动交易。

运行:py -m streamlit run app.py    (本机 py = Python312 全路径)
颜色:A股习惯 红涨/利多、绿跌/利空。
"""
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from smon import chips as chipsmod
from smon import indicators, market, sources
from smon import position as posmod
from smon import score as scoremod
from smon import signals as sigmod
from smon.config import load_config

st.set_page_config(page_title="feifei_Ashare 盯盘", layout="wide")
CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")

UP, DOWN, FLAT = "#e23b3b", "#1aaf5d", "#8a8a8a"   # 红涨 / 绿跌 / 中性


@st.cache_resource
def get_cfg():
    return load_config(CFG_PATH)


@st.cache_data(ttl=1800, show_spinner=False)
def get_edf(code, end):
    cfg = get_cfg()
    df = sources.fetch(code, cfg.start, end, source=cfg.data.source, cfg=cfg)
    if df is None or df.empty or len(df) < 30:
        return None
    return indicators.enrich(df, cfg)


@st.cache_data(ttl=1800, show_spinner=False)
def get_regime(end):
    return market.get_regime(get_cfg(), end)


@st.cache_data(ttl=1800, show_spinner=False)
def get_chips(code, end):
    edf = get_edf(code, end)
    return chipsmod.compute(edf, get_cfg()) if edf is not None else None


def analyze(code, cfg, end, regime=None):
    edf = get_edf(code, end)
    if edf is None:
        return None
    last = edf.iloc[-1]
    s = cfg.stock(code)
    ch = get_chips(code, end)
    sig = sigmod.evaluate(edf, cfg, market_regime=regime, chips=ch)
    return {
        "code": str(code).split(".")[0].zfill(6),
        "name": s.name if s else "",
        "edf": edf, "last": last, "chips": ch,
        "close": float(last["close"]), "pct": float(last["pct_chg"]),
        "score": scoremod.score_stock(edf, cfg, market_regime=regime, chips=ch),
        "sig": sig,
        "pos": posmod.annotate(s, edf, sig, cfg),
    }


def sig_text(sg):
    parts = []
    if sg["buy_level"]:
        parts.append(f"🔴{sg['buy_level']}买")
    if sg["sell_level"]:
        parts.append(f"🟢{sg['sell_level']}卖")
    return " ".join(parts) if parts else "—"


def color_pct(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    return f"color:{UP}" if v > 0 else (f"color:{DOWN}" if v < 0 else f"color:{FLAT}")


WT_CN = {"WEEKLY_BULL": "周线多头", "WEEKLY_BEAR": "周线空头", "WEEKLY_NEUTRAL": "周线中性"}
POS_CN = {"HOLDING": "持有", "EMPTY": "空仓", "WATCHING": "观察"}


def pos_label(pos):
    if not pos:
        return "空仓"
    s = pos.get("position_status", "EMPTY")
    cn = POS_CN.get(s, s)
    pnl = pos.get("your_pnl")
    return f"{cn}{pnl:+.0f}%" if (s == "HOLDING" and pnl is not None) else cn


# ============================================================ 侧边栏
cfg = get_cfg()
end = cfg.effective_end()
codes = cfg.codes()
st.sidebar.title("feifei_Ashare 盯盘")
st.sidebar.caption(f"数据截至 {end} · 自选股 {len(codes)} 只")
view = st.sidebar.radio("视图", ["📊 总览", "🔍 单股详情"])
if st.sidebar.button("🔄 刷新数据(清缓存)"):
    get_edf.clear()
    st.rerun()
st.sidebar.info("定位:监测/提醒,给人决策用,非自动交易。所有信号有滞后与失效可能,风险自负。")

# 大盘环境(全局,先算)
reg = get_regime(end)
regime = reg["regime"]

# 预加载全部(带进度)
results = []
prog = st.sidebar.progress(0.0, text="加载中…")
for i, c in enumerate(codes):
    r = analyze(c, cfg, end, regime=regime)
    if r:
        results.append(r)
    prog.progress((i + 1) / len(codes), text=f"加载 {c}")
prog.empty()

# 环境区横幅(规范 6.4:常驻顶部)
reg_color = {"RISK_ON": UP, "NEUTRAL": FLAT, "RISK_OFF": DOWN}.get(regime, FLAT)
idx_txt = " · ".join(f"{c} {market.REGIME_CN.get(s['regime'],'')}({s['pct_chg']:+.2f}%)"
                     for c, s in reg.get("indices", {}).items())
st.markdown(
    f"#### 🌐 大盘环境:<span style='color:{reg_color}'>**{regime} "
    f"{market.REGIME_CN.get(regime,'')}**</span>　<small style='color:#888'>{idx_txt}</small>",
    unsafe_allow_html=True)
if regime == "RISK_OFF":
    st.error("大盘走弱:买入信号已暂停,卖出信号升级。")
elif regime == "NEUTRAL":
    st.caption("大盘中性:买入信号降一级,谨慎。")

# ============================================================ 总览
if view == "📊 总览":
    st.subheader("📊 自选股总览(按综合分排序)")
    st.caption("已含:大盘环境(顶部)+ 周线背景 + 持仓盈亏。持仓列据 config.yaml 的持仓字段。")
    rows = []
    for r in sorted(results, key=lambda x: x["score"]["total"], reverse=True):
        s, sg = r["score"], r["sig"]
        rows.append({
            "代码": r["code"], "名称": r["name"], "现价": round(r["close"], 2),
            "涨跌%": round(r["pct"], 2), "综合分": s["total"], "档": s["band"],
            "趋势": s["trend"], "量能": s["volume"], "位置": s["position"],
            "周线背景": WT_CN.get(r["last"].get("weekly_trend"), "—"),
            "120日分位": round(r["sig"]["pos_pctile"], 0),
            "持仓": pos_label(r.get("pos")),
            "信号": sig_text(sg),
        })
    df = pd.DataFrame(rows)
    sty = (df.style
           .map(color_pct, subset=["涨跌%", "综合分", "趋势", "量能", "位置"])
           .format({"现价": "{:.2f}", "涨跌%": "{:+.2f}", "综合分": "{:+.1f}",
                    "趋势": "{:+.0f}", "量能": "{:+.0f}", "位置": "{:+.0f}",
                    "120日分位": "{:.0f}"}))
    st.dataframe(sty, width="stretch", hide_index=True, height=38 * (len(rows) + 1))
    st.caption("打分档:强多>50 / 偏多20~50 / 中性±20 / 偏空-50~-20 / 强空<-50。"
               "筹码桶(估算)+ 大盘环境 + 盈亏比闸门已接;持仓/确认 P5。仅为信号上下文,非买卖指令。")

# ============================================================ 单股详情
else:
    label = {r["code"]: f"{r['code']} {r['name']}" for r in results}
    pick = st.sidebar.selectbox("选择个股", [r["code"] for r in results],
                                format_func=lambda c: label.get(c, c))
    r = next((x for x in results if x["code"] == pick), None)
    if r is None:
        st.warning("无数据"); st.stop()
    edf, last, s, sg = r["edf"], r["last"], r["score"], r["sig"]

    # 顶部
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(f"{r['code']} {r['name']}", f"{r['close']:.2f}", f"{r['pct']:+.2f}%")
    c2.metric("综合分", f"{s['total']:+.1f}", s["band"])
    c3.metric("周线背景", WT_CN.get(last.get("weekly_trend"), "—"))
    c4.metric("120日位置分位", f"{sg['pos_pctile']:.0f}%")
    light = sig_text(sg)
    c5.metric("当前信号", light if light != "—" else "无")

    # 信号区
    if sg["buy_level"] or sg["sell_level"]:
        msgs = []
        if sg["buy_level"]:
            rr = sg.get("risk_reward")
            rrs = f"(盈亏比{rr:.1f})" if rr is not None else ""
            msgs.append(f"**🔴 {sg['buy_level']} 买入**{rrs} — " + " / ".join(sg["buy_rules"]))
        if sg["sell_level"]:
            msgs.append(f"**🟢 {sg['sell_level']} 卖出** — " + " / ".join(sg["sell_rules"]))
        st.warning("　|　".join(msgs))

    # 持仓区(规范第9章:个性化"对你"的动作)
    pos = r.get("pos") or {}
    pstatus = pos.get("position_status", "EMPTY")
    head = POS_CN.get(pstatus, pstatus)
    if pstatus == "HOLDING" and pos.get("cost_price"):
        head += f" · 成本 {pos['cost_price']:.2f}"
    if pos.get("action_for_you"):
        st.info(f"**👤 给你的动作({head}):** {pos['action_for_you']}")
    if pstatus == "HOLDING":
        h1, h2, h3, h4 = st.columns(4)
        pnl = pos.get("your_pnl")
        h1.metric("当前盈亏", f"{pnl:+.1f}%" if pnl is not None else "—")
        h2.metric("移动止盈线", f"{pos['trailing_tp']:.2f}" if pos.get("trailing_tp") else "—")
        h3.metric("实际离场线", f"{pos['effective_exit']:.2f}" if pos.get("effective_exit") else "—")
        h4.metric("止盈分级", pos.get("tp_level") or "—")

    g1, g2, g3 = st.columns(3)
    with g1:
        st.markdown("**趋势区**")
        align = "多头排列" if last.get("ma_bull_aligned") else (
            "空头排列" if last.get("ma_bear_aligned") else "均线纠缠")
        st.write(f"- 排列:{align}")
        st.write(f"- MA5/20/60/250:{last.get('ma5',float('nan')):.2f} / "
                 f"{last.get('ma20',float('nan')):.2f} / {last.get('ma60',float('nan')):.2f} / "
                 f"{last.get('ma250',float('nan')):.2f}")
        ll = last.get("lifeline")
        dist_ll = (r["close"] - ll) / ll * 100 if pd.notna(ll) else float("nan")
        st.write(f"- 生命线:{ll:.2f}(距 {dist_ll:+.1f}%)")
        st.write(f"- MACD:DIF {last.get('dif',float('nan')):.2f} / DEA {last.get('dea',float('nan')):.2f} / "
                 f"柱 {last.get('macd_hist',float('nan')):.2f}"
                 + ("　金叉" if last.get("macd_golden_cross") else "")
                 + ("　死叉" if last.get("macd_dead_cross") else ""))
    with g2:
        st.markdown("**量能 / 位置区**")
        st.write(f"- 换手率:{last.get('turnover',float('nan')):.2f}% "
                 f"({last.get('turnover_tier','—')})")
        vr = (last.get("volume", float("nan")) / last.get("vol_ma5")
              if last.get("vol_ma5") else float("nan"))
        st.write(f"- 量比(/5日均量):{vr:.2f}")
        pv = ("价涨量增" if last.get("price_up_vol_up") else
              "价跌量增" if last.get("price_down_vol_up") else
              "价涨量缩" if last.get("price_up_vol_down") else "—")
        st.write(f"- 量价配合:{pv}" + ("　放量滞涨⚠" if last.get("vol_stagnant") else ""))
        cs = last.get("chandelier_stop")
        dist_atr = (r["close"] - cs) / r["close"] * 100 if pd.notna(cs) else float("nan")
        st.write(f"- ATR吊灯止损:{cs:.2f}(距 {dist_atr:+.1f}%)"
                 + ("　已触发⚠" if last.get("atr_stop_triggered") else ""))
    with g3:
        st.markdown("**震荡区(仅辅助)**")
        st.write(f"- KDJ:K {last.get('kdj_k',float('nan')):.1f} / D {last.get('kdj_d',float('nan')):.1f} / "
                 f"J {last.get('kdj_j',float('nan')):.1f}")
        st.write(f"- RSI14:{last.get('rsi14',float('nan')):.1f}")
        divs = [n for n, k in [("MACD顶背离", "macd_top_divergence"),
                               ("MACD底背离", "macd_bottom_divergence"),
                               ("RSI顶背离", "rsi_top_divergence"),
                               ("RSI底背离", "rsi_bottom_divergence")] if last.get(k)]
        st.write("- 背离:" + ("、".join(divs) if divs else "无"))
        st.markdown("**周线背景(v2.1)**")
        st.write(f"- {WT_CN.get(last.get('weekly_trend'),'—')} | 周MA20 "
                 f"{last.get('w_ma20',float('nan')):.2f} | 周DIF {last.get('w_dif',float('nan')):.2f}")

    # K线图(近120日)+ 均线 + 吊灯止损
    st.markdown("**K线 + 均线 + ATR吊灯止损(近120日)**")
    d = edf.tail(120)
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=d["date"], open=d["open"], high=d["high"], low=d["low"], close=d["close"],
        name="K线", increasing_line_color=UP, decreasing_line_color=DOWN))
    for ma, col in [("ma20", "#f5a623"), ("ma60", "#4a90d9")]:
        if ma in d:
            fig.add_trace(go.Scatter(x=d["date"], y=d[ma], name=ma.upper(),
                                     line=dict(width=1, color=col)))
    if "chandelier_stop" in d:
        fig.add_trace(go.Scatter(x=d["date"], y=d["chandelier_stop"], name="吊灯止损",
                                 line=dict(width=1, color="#999", dash="dot")))
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10),
                      xaxis_rangeslider_visible=False, legend=dict(orientation="h"))
    st.plotly_chart(fig, width="stretch")

    # 筹码分布(P4,估算)
    ch = r["chips"]
    if ch:
        st.markdown("**🎯 筹码分布(估算,仅供参考)**")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("获利盘比例", f"{ch['profit_ratio'] * 100:.0f}%")
        k2.metric("平均成本", f"{ch['cost_50']:.2f}")
        k3.metric("90集中度", f"{ch['concentration']:.2f}" if ch["concentration"] is not None else "—",
                  "高度集中" if ch["concentrated"] else ("发散" if ch["dispersed"] else "中等"))
        cap = sg.get("capital")
        k4.metric("主力动向(估)", cap if cap else "—")
        cf = go.Figure()
        cf.add_trace(go.Bar(y=ch["centers"], x=ch["weights"], orientation="h",
                            marker_color="#c9a227", name="筹码"))
        cf.add_hline(y=r["close"], line=dict(color=UP, width=1.5),
                     annotation_text="现价", annotation_position="right")
        cf.add_hline(y=ch["cost_50"], line=dict(color="#4a90d9", width=1, dash="dot"),
                     annotation_text="平均成本", annotation_position="right")
        cf.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10),
                         xaxis_title="持仓占比", yaxis_title="价位", showlegend=False)
        st.plotly_chart(cf, width="stretch")

    # 指标达标清单(规范 0.1:输出哪些条件满足,供逐项核对)
    st.markdown("**📋 指标达标清单**(✅满足 / ⬜未满足 / ▸数值)")
    groups = sigmod.feature_status(edf, chips=r["chips"])
    cols = st.columns(3)
    for idx, (gname, items) in enumerate(groups):
        with cols[idx % 3]:
            lines = [f"**{gname}**"]
            for name, status, note in items:
                if isinstance(status, bool):
                    txt = f"{'✅' if status else '⬜'} {name}"
                    if status and note:
                        txt += f" <small style='color:#888'>· {note}</small>"
                else:
                    txt = f"▸ {name}:**{status}**"
                    if note:
                        txt += f" <small style='color:#888'>· {note}</small>"
                lines.append(txt)
            st.markdown("  \n".join(lines), unsafe_allow_html=True)

    # 评分构成
    with st.expander("综合分构成与理由"):
        reasons = s["reasons"]
        st.write(f"**趋势 {s['trend']:+.0f}**:" + "; ".join(reasons["trend"]) if reasons["trend"] else "趋势:—")
        st.write(f"**量能 {s['volume']:+.0f}**:" + "; ".join(reasons["volume"]) if reasons["volume"] else "量能:—")
        st.write(f"**位置 {s['position']:+.0f}**:" + "; ".join(reasons["position"]) if reasons["position"] else "位置:—")
        st.write((f"**筹码 {s['chip']:+.0f}**:" + "; ".join(reasons.get("chip", [])))
                 if reasons.get("chip") else f"筹码:{s['chip']:+.0f}")
        st.write(f"**微调** {s['adj']:+.0f}")
