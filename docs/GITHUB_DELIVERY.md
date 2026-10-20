# GitHub 源码与公开交付边界

仓库：<https://github.com/fangbm/Proj.DigitalLife>。

当前是 v0.2.3 / Phase 1.2.2 Stage C 安全停止修复交付。完整结论见 [安全修复报告](PHASE1_2_2_STOP_FIX_REPORT.md)，状态契约见 [停止屏障](PHASE1_2_2_STOP_BARRIER.md)。正式24小时尚未获得批准，也尚未执行；正式九项验收均为 NOT OBSERVED。上传仓库不代表授权运行。

本次源码提交保留与最新真实预检一致的39个运行文件、原角色与启动器。Git发布仅补充忽略规则和发布说明，不修改执行逻辑。当前源码294项离线测试通过；发布前会再从GitHub独立拉取执行离线测试。

`.gitattributes` 禁止自动转换行尾，保证Windows/Unix检出均保留冻结文件的原始SHA-256。

预发布 `v0.2.3-stage-c-safety-fix` 提供冻结源码ZIP、wheel、脱敏公开证据ZIP和仅覆盖这些公开资产的SHA256SUMS.txt。冻结ZIP是Stage C原始交付快照；Git仓库另外包含本发布说明与忽略规则。运行源码、测试、安全策略、角色和预算保持一致。

公开验证只证明交付完整性及离线回归。SQLite、PNG、控件树、原始周期JSONL、API账本、私有哈希清单及DPAPI密钥不上传；需要这些材料的深度因果复验只能在持有私有证据的本地环境执行。历史报告中的本地证据路径是审计引用，不代表远端提供这些私有文件。

仓库保留旧CLI/启动脚本以兼容历史用法。Phase 1.2.2正式验收入口是 `data/start-soak.ps1`，Settings范围与停止屏障保持；旧启动脚本不用于绕过新会话批准。示例配置仅含占位密钥，实际凭证由操作者在本地配置。

离线回归（不调用真实模型、不控制设备）：

```powershell
$env:PYTHONPATH='src;.'
python -m unittest discover -s tests -v
```

公开包完整性验证：

```powershell
$env:PYTHONPATH='src;.'
python -m acceptance.verify_continuation --public path/to/public-evidence.zip
```

本轮不启动24小时或永久后台Agent，不扩展Android权限，不开发Phase1.3。新候选仍待用户携新指纹单独明确授权。
