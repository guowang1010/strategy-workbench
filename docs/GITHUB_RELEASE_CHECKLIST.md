# GitHub 公开发布清单

## 上传前必须完成

- [ ] 确认仓库所有权、作者署名和联系方式。
- [ ] 选择并添加许可证。若希望他人可自由使用和贡献，可考虑 MIT/Apache-2.0；涉及专利授权时优先咨询专业人士。未添加许可证默认并不等于开源授权。
- [ ] 用 `git status --ignored` 检查 `.gitignore`，确认数据库、WAL/SHM、日志、导出物、抓取缓存、虚拟环境和 `node_modules` 未进入提交。
- [ ] 执行一次密钥扫描，并检查 Git 历史；仅删除工作区文件不能清除历史中的秘密。
- [ ] 确认样例行情数据的来源、授权与再分发条件；无法确认时不要随仓库发布。
- [ ] 检查截图、文档和数据库中是否含账户、持仓、用户名、本机路径或其他隐私信息。
- [ ] 后端测试、前端生产构建和启动健康检查全部通过。

## GitHub 仓库设置建议

- Description：`本地优先的 A 股日线策略回测、参数稳健性诊断与交易计划工作台`
- Topics：`backtesting`、`quantitative-finance`、`fastapi`、`react`、`typescript`、`sqlite`、`a-share`
- 开启 Issues、Discussions、Security Advisories 与 Dependabot alerts。
- 为默认分支开启保护：PR 合并、测试通过、禁止强推。
- 首个 Release 附版本说明、截图、已知限制和升级方式。

## 推荐发布内容

- `README.md`：项目价值、截图、快速开始、功能和风险声明。
- `帮助手册.md`：完整用户操作手册。
- `CONTRIBUTING.md`：贡献流程与回测代码约束。
- `SECURITY.md`：安全报告方式和本地部署边界。
- `CODE_OF_CONDUCT.md`：社区行为准则。
- `.github/`：Issue 和 PR 模板。

## 不建议发布

- `backend/data/*.db*`
- `backend/*.log`、`backend/exports/`
- `frontend/node_modules/`、`frontend/dist/`
- `frontend/ui_*.png` 等批量验收截图
- 临时 `_review_*.py`、本机虚拟环境和缓存目录

