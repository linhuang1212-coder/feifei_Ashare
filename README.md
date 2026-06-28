# feifei_Ashare

A股自选股技术指标**盘后**监测系统(实现《规则规范 v2.0》)。每交易日收盘后批算一次,
对自选股产出 **信号提醒 / 打分排序 / 仪表盘** 三类输出。**不做盘中/实时。**

> 完整设计、评审、分阶段计划见 [DESIGN.md](DESIGN.md)。免责声明:本系统仅为技术指标的
> 客观计算与提示,**不构成任何投资建议**;所有信号有滞后与失效可能,决策风险自负。

## 定位
- **运行时完全独立**(零跨仓依赖)。
- 数据上**只读复用** China_quant 的 `stock_data.db`(绝不写),代码上 **fork(复制)** 自
  quant-pipeline 的已验证模块(绝不 import / 反向依赖)。

## 运行环境
本机 Python 在非 PATH 位置,用完整路径调用(含量化全家桶):

    C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe

记 `PY` 为上面这个解释器。安装依赖:

    "%PY%" -m pip install -r requirements.txt

## 快速开始(P0 数据底座)

    "%PY%" run.py fetch 600519

取 600519 的前复权日线 + 换手率:主体读本地 `daily_kline_v2`(连续到建库日 2026-06-05),
再用 akshare 把缺口补到今天,缝合点按复权因子重锚对齐(消除两段 qfq 基准跳变)。

## 数据层要点(已实测 2026-06-27)
- `daily_kline_v2`:连续前复权日线,列名为**正常 UTF-8 中文**(`日期/开盘/最高/最低/收盘/
  preclose/成交量/成交额/换手率/涨跌幅/ST标记/收盘_nfq/peTTM/pbMRQ`),`code` 为 6 位纯数字,
  覆盖到 **2026-06-05**(600519 实测 2530 行)。
- 大盘指数:本地库**无**上证 000001 / 创业板 399006,仅有沪深300/中证1000/中证2000 且只有
  `close` 列 → regime(第6章)指数走 tushare/akshare,或用沪深300 当大盘代理(P3 决定)。
- 主力资金:免费源无,用换手 + 量价 + 筹码近似(规范已预案)。

## 进度
- [x] **P0 数据底座** —— sources(只读 local_db + topup 重锚) / config / 日志 / CLI
- [x] **P1 指标 + 打分 + 基础 B/S 三级信号** —— indicators / score / signals / `run.py score`
- [x] **P1.5 事件驱动回测器** —— costs / backtest(无未来函数 / 持仓路径依赖 / 真实成本 / OOS) / `run.py backtest`
- [x] **P3.5 周线背景(v2.1)** —— weekly.py 因果合成;v2.1 门控经 A/B 中性票池回测**证伪**→默认关闭,周线背景仅保留显示
- [x] **仪表盘(Streamlit)** —— app.py 总览(打分/信号/周线/大盘环境)+ 单股详情卡 + **📋指标达标清单**
- [x] **指标达标清单** —— `run.py check` / 仪表盘详情页:逐项 ✓/✗ 列出每只股满足哪些条件(规范0.1)
- [x] **P3a 大盘环境过滤** —— market.py(akshare 取上证/创业板→RISK_ON/NEUTRAL/RISK_OFF)+ 信号升降级 + 打分修正 + 盈亏比闸门(rr<1.5 丢弃)
- [x] **P4 筹码分布** —— chips.py(三角分布+换手衰减,因果)→ 获利盘/成本/集中度/筹码峰 + 打分筹码桶 + CAPITAL 动向 + 仪表盘筹码区&分布图
- [x] **P5a 持仓感知 + 止盈** —— position.py:持仓盈亏/个性化动作(空仓看买、持有看卖止盈)+ 移动止盈/effective_exit + TP1/2/3 分级;CLI/仪表盘持仓区
- [x] **P6 全市场扫描** —— sources.build_cache(daily_kline_v2→feifei.db 加 code+date 索引,~18s/440万行)+ list_universe(5526 只,不过滤)+ 两阶漏斗 `run.py screen`(一阶缓存秒级粗筛→二阶 top-N topup 到今天精算重排);local_db 读取优先走缓存,pos_pctile 改 rolling.rank 提速
- [ ] P5b 信号确认(次日)+ 失败/冷静期状态机(state.py,需逐日持久化)
- [ ] P2 量价细分 + ATR 吊灯止损精化 + 飞书告警
- [ ] P3b 板块强弱(申万)+ 钝化检测调权 + 全市场扫描的批量补鲜/仪表盘排行标签
- [ ] P6 阈值校准 + 每日定时任务

> 定位:**监测/提醒**(给人决策),非自动交易。回测=信号质量体检,不以跑赢大盘为目标。

## 常用命令

    "%PY%" run.py score                    # 全自选股打分排序 + 信号 + 大盘环境
    "%PY%" run.py check [代码]             # 指标达标清单(逐项 ✓/✗,自己过一遍)
    "%PY%" run.py backtest [代码]          # 事件驱动回测(信号质量体检)
    "%PY%" run.py cache                    # 建/重建全市场缓存(首次或 daily_kline_v2 更新后)
    "%PY%" run.py screen --top 30          # 全市场两阶扫描选股(需先 cache);--short 取强空
    "%PY%" -m streamlit run app.py         # 仪表盘,浏览器开 http://localhost:8501

## 数据来源与新鲜度
- 历史:只读 China_quant `daily_kline_v2`(全市场 ~5526 只,前复权,到建库日)。
- 全市场提速:`cache` 把它复制进 `data/feifei.db` 并加 code+date 索引(local_db 读取自动优先用)。
- 新鲜度:个股/自选/screen 二阶用 tushare(secrets.local.yaml 配 token)topup 到今天,失败回退 akshare。
- 密钥:`secrets.local.yaml`(已 gitignore)或环境变量 `TUSHARE_TOKEN`。

## 模块(规划)
`smon/`:`config` 配置中心 · `logsetup` 日志 · `sources` 数据源(✅) · `indicators` 指标 ·
`turnover` 换手 · `chips` 筹码 · `market` 大盘regime · `signals` 四类信号 · `takeprofit` 止盈 ·
`position` 持仓 · `state` 信号状态机 · `score` 打分 · `pipeline` 编排 · `alert` 推送 ·
`store` 存储 · `backtest` 事件驱动回测。`run.py` CLI · `app.py` Streamlit。
