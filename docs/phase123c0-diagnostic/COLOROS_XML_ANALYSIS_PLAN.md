# C0 原生语义离线分析计划

1. 私有原件 SHA、采集时刻和授权/命令哈希关联，先证来源，不信文件名。
2. Pillow 校验实际 PNG 宽高/完整性/像素上限，不能只信 wm size。
3. XML 禁 DTD/ENTITY；统计节点和缺失属性，逐节点 clickable/enabled/focusable/password/editable/visible-to-user/checkable/long-clickable/bounds/package/class。
4. assess_xml 检查布尔声明、屏内 bounds、方向、Settings 归属、时效和前后场景，不补写缺失属性。文本/描述与动态字段仍未证明，不能使用API35特例。
5. assess_window_graph 保留原 Window/Surface/Input 联合结果、未知 OEM 和未归属层、权限窗口、Activity/焦点及采集时间；真实 SurfaceFlinger 父子/Z和原子屏幕一致性仍需额外证明。
6. analyze_pair 输出诊断几何/节点/原生差距，GUI_EXECUTION_SEMANTICS_TRUSTED 永远 false。PNG与XML之间不是原子采样，模型看图不能替代原生属性。采集完成结果 DIAGNOSTIC_CAPTURE_COMPLETED 不自动进入可执行观察 provider。

本轮使用合成内容验证分析流程；Find X6 PNG/XML NOT EXECUTED。没有把旧 metadata 证据重新分类成原始页面采集证据。
