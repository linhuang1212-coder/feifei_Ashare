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

from smon import indicators, sources
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


def analyze(code, cfg, end):
    edf = get_edf(code, end)
    if edf is None:
        return None
    last = edf.iloc[-1]
    s = cfg.stock(code)
    return {
        "code": str(code).split(".")[0].zfill(6),
        "name": s.name if s else "",
        "edf": edf, "last": last,
        "close": float(last["close"]), "pct": float(last["pct_chg"]),
        "score": scoremod.score_stock(edf, cfg),
        "sig": sigmod.evaluate(edf, cfg),
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

# 预加载全部(带进度)
results = []
prog = st.sidebar.progress(0.0, text="加载中…")
for i, c in enumerate(codes):
    r = analyze(c, cfg, end)
    if r:
        results.append(r)
    prog.progress((i + 1) / len(codes), text=f"加载 {c}")
prog.empty()

# ============================================================ 总览
if view == "📊 总览":
    st.subheader("📊 自选股总览(按综合分排序)")
    st.caption("环境区:大盘 regime 待 P3 接入;周线背景已常驻(v2.1)。")
    rows = []
    for r in sorted(results, key=lambda x: x["score"]["total"], reverse=True):
        s, sg = r["score"], r["sig"]
        rows.append({
            "代码": r["code"], "名称": r["name"], "现价": round(r["close"], 2),
            "涨跌%": round(r["pct"], 2), "综合分": s["total"], "档": s["band"],
            "趋势": s["trend"], "量能": s["volume"], "位置": s["position"],
            "周线背景": WT_CN.get(r["last"].get("weekly_trend"), "—"),
            "120日分位": round(r["sig"]["pos_pctile"], 0),
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
               "筹码分 P4 未计、大盘环境 P3 未接,故为信号上下文非买卖指令。")

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
            msgs.append(f"**🔴 {sg['buy_level']} 买入** — " + " / ".join(sg["buy_rules"]))
        if sg["sell_level"]:
            msgs.append(f"**🟢 {sg['sell_level']} 卖出** — " + " / ".join(sg["sell_rules"]))
        st.warning("　|　".join(msgs))

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

    # 评分构成
    with st.expander("综合分构成与理由"):
        reasons = s["reasons"]
        st.write(f"**趋势 {s['trend']:+.0f}**:" + "; ".join(reasons["trend"]) if reasons["trend"] else "趋势:—")
        st.write(f"**量能 {s['volume']:+.0f}**:" + "; ".join(reasons["volume"]) if reasons["volume"] else "量能:—")
        st.write(f"**位置 {s['position']:+.0f}**:" + "; ".join(reasons["position"]) if reasons["position"] else "位置:—")
        st.write(f"**筹码** {s['chip']:+.0f}(P4 未实现) · **微调** {s['adj']:+.0f}")
