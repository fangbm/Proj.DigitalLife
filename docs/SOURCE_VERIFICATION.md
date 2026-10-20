# 公开完整性检查与私有因果复验

`acceptance/verify_continuation.py` 的非敏感统计依赖现在随源码提供于 `acceptance/evidence_metrics.py`；不再从历史 `data/phase122/analyze.py` 导入代码。

全新解压后的离线检查（Python 3.11+，Pillow 已安装）：

```powershell
$env:PYTHONPATH='src;.'
python -m acceptance.verify_continuation --self-check
python -m phonelife.cli --help
python -m unittest discover -s tests -v
python -m acceptance.verify_public PATH_TO_PUBLIC_EVIDENCE.zip
```

公开工具检查 ZIP CRC、完整路径清单、重复条目和逐文件 SHA-256。`live_device_behavior_reproved=false` 始终明确返回：一个自洽的公开包不能独立证明真实设备动作或私有 API 内容。

深度因果复验需要原私有 SQLite、原图/控件树及对应版本的原始冻结源码、测试日志与历史完整性清单。v0.2.2 的修复源码包单独保留，供匹配版本复核。`python -m acceptance.verify_continuation PRIVATE_COHORT` 默认只读，不覆盖原证明文件；`--output NEW_DIRECTORY` 才写新的派生结果。缺失私有输入时应明确失败，不能把公开检查提升为因果通过。

源码归档工具使用固定条目时间和逐文件清单，干净目录测试检查本地 import 来源、CLI、验证器自检和全部离线测试。源码包不包含私有数据库、原始截图、完整树、DPAPI 文件或模型 API 密钥。
