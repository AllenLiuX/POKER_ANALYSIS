"""HU（单挑）人机对战引擎：从翻前打到河牌，无状态 + 确定性（deal_seed）。

复用现有框架：
- 翻前对手策略与判分复用 data/ranges 的 6-max BTN/BB 频率（作 HU 近似，与翻后训练器一致）；
- 翻后对手策略与判分复用启发式引擎（texture / handstrength / heuristics + range advantage）。

对手底牌不下发前端，由 deal_seed 服务端再推导，防偷看。
"""
