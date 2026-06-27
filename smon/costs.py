"""A股真实交易成本(可配,规范 11.3 要求回测含真实成本)。

买入成本 = 佣金 + 过户费 + 滑点;卖出成本 = 佣金 + 过户费 + 印花税(仅卖出) + 滑点。
默认为常见档位,实盘前务必按券商实际校准。最低佣金 5 元在 rate 模型里暂不计
(等 P5+ 引入资金/手数后再精确),对高价股影响可忽略。
"""
from dataclasses import dataclass


@dataclass
class CostModel:
    commission: float = 0.00025     # 佣金 双边
    min_commission: float = 5.0     # 最低佣金(rate 模型暂未使用)
    stamp_tax: float = 0.0005       # 印花税 仅卖出
    transfer_fee: float = 0.00001   # 过户费 双边
    slippage: float = 0.0005        # 滑点 单边

    def buy_rate(self) -> float:
        return self.commission + self.transfer_fee + self.slippage

    def sell_rate(self) -> float:
        return self.commission + self.transfer_fee + self.stamp_tax + self.slippage

    def round_trip_rate(self) -> float:
        return self.buy_rate() + self.sell_rate()


def from_cfg(cfg) -> CostModel:
    c = getattr(cfg, "costs", {}) or {}
    return CostModel(
        commission=float(c.get("commission", 0.00025)),
        min_commission=float(c.get("min_commission", 5.0)),
        stamp_tax=float(c.get("stamp_tax", 0.0005)),
        transfer_fee=float(c.get("transfer_fee", 0.00001)),
        slippage=float(c.get("slippage", 0.0005)),
    )
