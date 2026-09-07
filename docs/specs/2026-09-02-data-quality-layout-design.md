# 数据中心质量概览与数据集详情合并排版设计

日期：2026-09-02
页面：`/data`

## 决定

采用单区域方案：

1. 页面顶部“数据质量概览 + 数据集详情”两个独立面板合并为一个“数据质量”区域。
2. 区域内上段为 4 个质量概览数字；下段为“当前数据集”详情。
3. 不展开表格行；点击某行“查看版本”按钮后在下方同一区域刷新：
   数据集名称、版本、类别、路径别名、行数、日期范围、质量状态与说明、
   注册质量检查、Schema 字段、不可变版本历史。
4. 标题、标签、值与检查清单使用统一字号/字重，不再分大小两套面板样式。

## 范围

- 仅调整 `quantlab/web/pages/data.html`、`quantlab/web/assets/app.js`、
  `quantlab/web/assets/app.css` 及现有页面结构测试。
- 数据来源保持 `/api/datasets`、`/api/datasets/quality-summary`、
  `/api/datasets/{entity_id}/versions`，不读取整张 Parquet。
