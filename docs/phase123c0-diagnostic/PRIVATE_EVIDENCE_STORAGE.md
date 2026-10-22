# 私有存储合同

create_private_directory 只创建新绝对目录，拒绝相对路径、..、已有目录和父级 reparse/symlink。Windows 显式设置受保护 owner-only DACL，并用 Win32 owner/DACL API 校验当前账号 SID；继承到新文件的 ACE 也只允许该 owner 的完全权限。POSIX 检查 uid 和 0700/0600 权限。不会修复旧材料 ACL。

PrivateEvidence 在进入、写入前后复查目录 ACL、reparse 与文件身份。文件名只能是 32 位小写十六进制 request_id 加固定 png/xml 后缀，以 O_EXCL 创建，禁止覆盖、用户文件名和任意路径；字节写入后 flush/fsync。Windows 拒绝 UNC/ADS。独立审计库只存在同一私有目录，默认 0600/owner ACL，不触碰原生活库。

PNG 最多 16 MiB、1600 万解码像素，Pillow 验证和加载；XML 最多 2,000,000 字节、10000 node，禁用 DTD/ENTITY。格式有效不意味内容可信。半成品/写失败保留独立已消费审计，绝不覆盖或作为完成导出；只允许人工清理。

限制：不能抵抗同账号恶意并发替换、管理员取得所有权、磁盘/OS 全损或回滚备份。路径逐段校验加独占创建缩小风险，但不是完整 Windows 目录 handle 相对打开/锁定的抗恶意 TOCTOU 证明。真实启用前应复核目录 handle 级绑定、卷类型、DACL 和设备端尾进程；不因此放开当前硬禁用。
