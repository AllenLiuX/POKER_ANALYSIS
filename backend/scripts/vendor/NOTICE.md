# 第三方数据来源与许可

本目录下的 `pekarstas.ts`、`greenline.ts`、`poker_types.ts` 原样取自开源项目：

- **poker-charts** — https://github.com/AHTOOOXA/poker-charts
- **License**: MIT

它们是近 GTO 的**简化翻前图表**（离散策略，含 50/50 混合），用于本项目的
翻前范围数据来源。`scripts/import_ranges.py` 会把它们转换成
`backend/data/ranges/6max_100bb/` 下的逐类别频率 JSON。

生产使用的默认 provider 为 **greenline**（GreenCharts2024，100bb 6-max），
其把 open/3bet 统一标为 `raise`、flat 标为 `call`，语义与 100bb 现金局一致。
`pekarstas`（GGPoker 包）保留作对照，但其将顶级牌标为 `allin`，更像短码/推弃
风格，不适合 100bb，故未采用。

> 说明：这些是**简化图表**而非精确 solver 输出，UI 已标注"近 GTO / 开源图表"。
> 如需更精确，可用真 solver 导出替换（转换脚本的输入格式不变）。
