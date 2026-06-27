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
- [ ] P2 量价细分 + ATR 吊灯止损 + 告警
- [ ] P3 大盘/板块环境过滤 + 止盈/盈亏比
- [ ] P4 筹码分布 + CAPITAL 信号
- [ ] P5 持仓感知 + 信号确认状态机
- [ ] P6 阈值校准 + 每日定时任务

## 模块(规划)
`smon/`:`config` 配置中心 · `logsetup` 日志 · `sources` 数据源(✅) · `indicators` 指标 ·
`turnover` 换手 · `chips` 筹码 · `market` 大盘regime · `signals` 四类信号 · `takeprofit` 止盈 ·
`position` 持仓 · `state` 信号状态机 · `score` 打分 · `pipeline` 编排 · `alert` 推送 ·
`store` 存储 · `backtest` 事件驱动回测。`run.py` CLI · `app.py` Streamlit。
