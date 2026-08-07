"""翻后启发式引擎（透明规则近似，非精确 solver）。

模块划分：
- texture      —— 翻牌面纹理分类（干/湿、成对、同花性、连接性、高低）
- handstrength —— 英雄成手强度分级 + 听牌识别（借助 eval7）
- heuristics   —— c-bet / 防守（MDF、赔率、bluff-to-value）决策建议
- scoring      —— 把用户动作与建议对比、容忍合理区间
- scenario     —— 生成 HU 单加注底池翻牌场景
- coach        —— 中文教学反馈（明确标注"启发式近似"）
"""
