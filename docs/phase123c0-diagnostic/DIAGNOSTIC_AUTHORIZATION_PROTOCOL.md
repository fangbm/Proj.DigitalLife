# PHYSICAL_DIAGNOSTIC_CAPTURE

独立 purpose 与 DPAPI optional entropy：Proj.DigitalLife PHYSICAL_DIAGNOSTIC_CAPTURE v1。不可复用物理执行批准、模型 key、模拟器指纹、USB 调试或配置开关。生产只提供解密读取函数，不提供签发命令。原凭证仅做密文完整性哈希，未解密或用于授权。

协议字段严格等集：schema、purpose、identity_sha256、operations、page_scope、not_before、expires_at、output_directory、nonce、operator_approved、operator_reference、reason、audit_id、category。拒绝重复 JSON 键、未知字段、非有限时间、布尔次数和空操作人引用。窗口最多 600 秒；PNG 最多 2 次，XML 最多 1 次。

本轮以测试专用 HMAC envelope 验证篡改、目的和完整字段传递。测试签名代码只存在测试中；envelope 必须 offline_fixture。它不是生产同意，也不是 DPAPI 签发证据。Prep 捕获器不接受真实 DPAPI 适配器或真实 backend，即使拿到有效密文也不能分发。

独立私有 diagnostic.sqlite3 的 permits 表固定 nonce→完整授权摘要；captures 主键固定 request_id。BEGIN IMMEDIATE + synchronous=FULL 在分发前消费次数，started、失败、取消和 unknown 均计入已消费；不同内容复用 nonce 拒绝。关闭并重开/进程异常退出后仍计数。不将失败请求自动转换成可重试。

未来人工仪式必须由明确的人类同意建立短期 artifact，不能由模型/AI 自签。DPAPI 限制同账号/通常同机器，并非抵抗相同 Windows 账号恶意代码的密码签名。系统所有者删除/回滚消费数据库或修改时钟不在本机协议能可靠抵抗的威胁边界；真实启用前必须审查批准与账本的固定位置、可信操作人界面和防回滚策略。

[Microsoft DPAPI 目的熵及用户范围说明](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)
