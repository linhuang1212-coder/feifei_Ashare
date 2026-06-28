"""配置中心 —— 改票/改阈值/改源都在 config.yaml,代码不硬编码(规范 0.6 / 11.4)。

密钥(tushare_token / feishu_webhook)读取优先级:
    环境变量 > secrets.local.yaml > config.yaml
secrets.local.yaml 已 .gitignore,密钥永不入库。
相对输出路径一律解析为相对 config.yaml 所在目录的绝对路径(从任何 cwd 跑都不跑偏)。
"""
import os
from dataclasses import dataclass, field, fields
from datetime import date
from pathlib import Path

import yaml


@dataclass
class DataCfg:
    source: str = "local_db"          # local_db(只读China_quant) | tushare | akshare
    local_db_path: str = r"C:\Users\Administrator\China_quant\data\stock_data.db"
    local_db_table: str = "daily_kline_v2"
    topup: bool = True                # 本地库停在建库日时,自动补到 end
    topup_source: str = "akshare"     # 缺口补源: akshare(免token) | tushare
    cache: bool = True


@dataclass
class StockCfg:
    code: str
    name: str = ""
    # 持仓状态(规范第 8 章);默认空仓
    position_status: str = "EMPTY"    # HOLDING | EMPTY | WATCHING
    cost_price: float = 0.0
    position_size: float = 0.0
    entry_date: str = ""


@dataclass
class AppConfig:
    data: DataCfg
    start: str
    end: str
    watchlist: list                   # List[StockCfg]
    periods: dict
    thresholds: dict
    scoring_weights: dict
    market_filter: dict
    take_profit: dict
    confirmation: dict
    board_adjustment: dict
    alerts: dict
    backtest: dict
    costs: dict
    multi_period: dict
    blunt_detection: dict
    project_dir: str
    db_path: str
    fig_dir: str
    log_level: str
    log_file: str
    tushare_token: str = ""
    feishu_webhook: str = ""

    def codes(self) -> list:
        return [s.code for s in self.watchlist]

    def stock(self, code: str) -> "StockCfg | None":
        c = str(code).split(".")[0].zfill(6)
        return next((s for s in self.watchlist if str(s.code).zfill(6) == c), None)

    def effective_end(self) -> str:
        """end 为空时返回今天(YYYY-MM-DD);用系统时钟,不脑推。"""
        return self.end.strip() or date.today().isoformat()


def _filter_kwargs(cls, d: dict) -> dict:
    """只保留 dataclass 认识的字段,多余键忽略(容错)。"""
    valid = {f.name for f in fields(cls)}
    return {k: v for k, v in (d or {}).items() if k in valid}


def _resolve(base: Path, p: str) -> str:
    pp = Path(p)
    return str(pp if pp.is_absolute() else (base / pp))


def _load_secret(key: str, env: str, project_dir: Path, raw: dict) -> str:
    """密钥读取优先级:环境变量 > secrets.local.yaml > config.yaml。"""
    if os.environ.get(env):
        return os.environ[env].strip()
    sec = project_dir / "secrets.local.yaml"
    if sec.exists():
        d = yaml.safe_load(sec.read_text(encoding="utf-8")) or {}
        if d.get(key):
            return str(d[key]).strip()
    return str(raw.get(key, "")).strip()


def load_config(path: str = "config.yaml") -> AppConfig:
    cfg_path = Path(path).resolve()
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    project_dir = cfg_path.parent

    data = DataCfg(**_filter_kwargs(DataCfg, raw.get("data", {})))
    watchlist = [StockCfg(**_filter_kwargs(StockCfg, s)) for s in raw.get("watchlist", [])]
    out = raw.get("output", {})
    log = raw.get("logging", {})

    return AppConfig(
        data=data,
        start=str(raw.get("start", "2023-01-01")),
        end=str(raw.get("end", "")),
        watchlist=watchlist,
        periods=dict(raw.get("periods", {})),
        thresholds=dict(raw.get("thresholds", {})),
        scoring_weights=dict(raw.get("scoring_weights", {})),
        market_filter=dict(raw.get("market_filter", {})),
        take_profit=dict(raw.get("take_profit", {})),
        confirmation=dict(raw.get("confirmation", {})),
        board_adjustment=dict(raw.get("board_adjustment", {})),
        alerts=dict(raw.get("alerts", {})),
        backtest=dict(raw.get("backtest", {})),
        costs=dict(raw.get("costs", {})),
        multi_period=dict(raw.get("multi_period", {})),
        blunt_detection=dict(raw.get("blunt_detection", {})),
        project_dir=str(project_dir),
        db_path=_resolve(project_dir, out.get("db_path", "data/feifei.db")),
        fig_dir=_resolve(project_dir, out.get("fig_dir", "out")),
        log_level=str(log.get("level", "INFO")),
        log_file=_resolve(project_dir, log.get("file", "logs/feifei.log")),
        tushare_token=_load_secret("tushare_token", "TUSHARE_TOKEN", project_dir, raw),
        feishu_webhook=_load_secret("feishu_webhook", "FEISHU_WEBHOOK", project_dir, raw),
    )
