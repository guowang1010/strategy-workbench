# 参与贡献

感谢你愿意改进波段策略回测工作台。讨论设计、报告缺陷、补充测试和提交代码都很有价值。

## 开始之前

- 功能建议和使用交流：QQ 群 `1131751562`。
- 明确的缺陷请使用 GitHub Bug Report 模板，并附最小复现步骤。
- 较大的功能或回测口径变化，请先开 Discussion/Issue 对齐设计，避免重复劳动。
- 请勿在 Issue、日志或截图中提交真实账户、持仓、密钥和其他敏感信息。

## 本地开发

```powershell
# 后端
python -m pip install -r backend/requirements.txt -r backend/requirements-dev.txt
cd backend
python -m pytest -q

# 前端
cd ../frontend
npm ci
npm run build
```

开发模式可分别运行 `python -m uvicorn main:app --reload --port 8765` 和 `npm run dev`。

## 提交要求

1. 一个 PR 聚焦一个主题，说明问题、方案、影响范围和验证方法。
2. 修复缺陷必须补充回归测试；交易时序、费用、复权或指标口径变化必须给出可复算示例。
3. 后端需通过完整 pytest，前端需通过 TypeScript 与生产构建。
4. 不提交数据库、日志、导出结果、抓取缓存、`node_modules`、虚拟环境或本机路径。
5. UI 改动建议附深色/浅色截图，并检查窄屏布局。

## 回测代码特别约定

- 信号只能使用当前及历史数据，禁止未来函数。
- 信号确认与成交必须保持 T+1 时序；成交限制、费用和滑点必须走统一实现。
- 不改变既有指标含义而不升级口径版本；任何兼容性变化都应写入 PR。
- “样本内最优”“样本外结果”和“事后 Oracle”必须明确区分，避免误导。

