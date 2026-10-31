# BUG 报告修复与专项回归

2026-10-31，修改的是原程序目录 `kerr-trpl/`。依据用户提供的 `qa/BUG_REPORT.md` 修复；外部测试报告及其被测副本没有改写。修改前源码备份在 `backups/bugfix-20261031-204207/`。

本次只运行报告涉及的缺陷回归：**68 项 Python 用例通过，17 项前端检查通过**；没有运行原有全套测试、约 900 项 QA、浏览器或桌面窗口，也没有连接仪器。原报告中的建议是待评估的修复方案，本次按实际代码和复现结果选择实现。

## P1 / P2

| 原报告编号 | 修改 | 专项证据 |
|---|---|---|
| E-01 首次环境准备 | 新增公共 bootstrap.ps1；按 py -3.12 → py -3 → python → python3 探测，先确认 64 位且版本 ≥3.12，再创建 venv；不再优先使用 AI 运行时目录。解释器路径以 ASCII JSON 传输，避免中文路径和 Windows PowerShell 5 引号问题 | E1 / E16 / E20，用真实 PowerShell 执行探测和替身启动 |
| P2-2 非有限 JSON 返回 500 | RequestValidationError 响应递归清洗 NaN/Infinity 和异常对象，正常返回 422 | 3 种非有限字面量 × 3 个接口，共 9 条 |
| P2-3 CSV 导出内存 | 直接迭代原始 records / ndarray，不再 serial() 整轮光谱；支持 NumPy 整数，逐帧校验并计算窗口均值 | 300 帧导出，额外峰值断言 <3 MiB；这是小规模上限回归，不是实测 25000 帧。仍在内存生成 CSV 文本，内存随 CSV 行数增长，不随整轮 1024 像素列表增长 |
| P2-4 标定轴不刷新 | load / apply 同步 live_axis，光谱仪记录变更清空 live_axis；旧实验冻结轴保持不变；点击使用与绘制相同的 live_axis | live_axis / discard；FE-11 |
| P2-5 / FE-01 token 不能自愈 | 失效 token 的 403 只刷新并重试一次，单飞刷新；识别 worker session 变化主动更新；其他 403 不盲重试 | 两条前端 token 场景 |
| P2-6 / FE-02 过期 run 被误判离线 | ApiError 携带 HTTP 状态，records 的 404/409 清除过期本地数据并重新轮询，不再显示 SERVICE OFFLINE | 前端 records 409，状态轮询仍成功 |
| P2-7 工作进程失效 | IPC 请求及时报告失败；WebSocket 干净 close(1011)，heartbeat 检查进程存活；前端显示重启提示，独立 health 路由可识别死 worker | 实际终止本测试自己创建的 worker，验证 HTTP 503 和 WS 1011；前端 503 提示/恢复。**不自动重建未知设备状态** |
| P2-8 饱和检查顺序 | 先过滤拟合时间窗再检查饱和，窗口外饱和不阻断拟合；前端汇总所选窗口饱和帧并提示调整起止时间 | 时间窗外/内饱和对照；FE-13 |
| P2-9 / E-02 启动中不能关窗 | 启动取消 Event；关闭立即放行，后台启动返回后清理拥有的子进程，主 GUI 不等待 30 s | 阻塞启动的关闭替身测试 |
| P2-10 / E-03 双入口竞态 | 自己的子进程退出后仍探测兄弟服务，成功后放弃已退出子进程的所有权，不关闭兄弟服务 | 并发启动赢家复用测试 |
| P2-11 / E-04 忙服务探测失败 | 用独立 identity 接口识别服务，避免等待 worker 的保存命令；退出状态查询可退避重试；超时不视为同意强杀正在写盘的服务 | 忙 worker 的身份探测不调用 status；暂时超时重试 |
| P2-12 / E-05 多窗口共享 owner | 桌面每个 checkout 单实例锁；第二次启动只尝试激活已有窗口 | 文件锁双实例检查，激活使用替身 |

## P3：状态、数据与数值

| 编号 | 修复 |
|---|---|
| F2 | IPC 保留错误类别对应的 HTTP 状态：参数/计划/导出校验 422，缺失 run/task/文件 404，设备状态/缓存过期冲突 409 |
| F3 / L3 | finalizing 或终态拒绝 Stop；最后一次停止判定与进入 finalizing 在同一锁内，避免 stopping 永久悬挂 |
| F4 / L5 / E10 | 新增 background_stop 和前端停止路由；桌面关闭时可停止背景采集 |
| F5 / S3 | ring 缺事件时发送 stream.gap 和恢复提示；snapshot / heartbeat 携带 schema_version、worker_session_id、timestamp_utc；前端收到 gap 重新同步 |
| F6 | save / discard / open / fit 不再依赖设备 connected，但仍拒绝与活动采集冲突 |
| F8 / L6 / L7 | run 或 open 更新曝光时同步 revision 并清除旧背景，重开实验恢复曝光记录 |
| F9 | 重复 pause（pausing/paused）与 resume（running）幂等 |
| L4 / S7 | 最后一个点后不进入暂停等待，直接收尾 |
| L9 / S5 | 拟合可协作取消，运行/排队上限 4；完成任务缓存和界面历史上限 64；已落盘结果不删除，重开只读取最近 64；未保存实验达到 64 条时要求先保存 |
| S1 / S2 | preview_stop 不再停止 jog；停止命令按 busy 类型匹配；discard 清除 latest 并恢复当前实时轴 |
| NUM BUG-02 / 09 / 10 | 估时采用与仿真台相同的加减速及预定位路径；移除 7 位小数位置量化误判，处理浮点末尾伪端点；plan_id 排除起始位置造成的估时变化，同时保留请求参数身份 |
| NUM BUG-03 / 04 / 15 / 16 | HDF 元数据、事件、记录 JSON 拒绝 NaN/Infinity；检查 committed 与数据长度、必要结构和终态白名单；损坏文件归一为 ValueError |
| NUM BUG-05 / 07 / 18 / 19 | CSV 来源不能包含换行；数值/必需字段校验统一并接受 NumPy 标量；HDF 拒绝浮点谱静默截断 |
| NUM BUG-08 | 构造失败只清理由本次创建且未初始化成功的 partial / log，绝不删除已有文件 |
| NUM BUG-06 / 11–14 / 17 / 20 | 拟合输入/曝光/卷积参数有限值校验、初值裁入边界；双指数组件不分离/幅度过小时警告；本模型 IRF FWHM 数值范围 0.0001–100 ps，并同步 UI 范围 |
| 重复 analysis ID | 明确报“已存在”，避免不明 h5py 异常 |

## P3：标定、前端及入口

- CAL-1 / CAL-3：超大曝光转 ValueError；空轴持久化也验证完整形状和来源，损坏时返回完整空标定及 warning。
- CAL-2：本程序读写共享锁；Windows 短暂共享冲突有限退避重试；失败保留旧文件、清理临时文件并给出可操作错误。
- CAL-11–13：成功恢复清旧 warning；帧访问返回副本；安装同步缓存 applied 标志。
- FE-03–08：显示 calibration_warning；422 提示字段和原因；非 JSON 导出错误显示 HTTP 状态；无标定禁用导出；失配背景取消勾选并清 subtract；禁用的空谱线不参与请求。
- FE-09–13：取消 Fit 会请求取消并压制晚到结果；单点 decay 绘制圆点；谱图点击与绘制同轴；复用 dialog 不重复 showModal；提示拟合时间窗内饱和帧。请求超时会释放前端单飞状态。
- E-06 / 07：CLI 校验端口 1024–65535；浏览器仅在匹配本 checkout/build 的服务就绪时打开，超时记录错误。
- E-08 / 09：关闭分层等待/terminate/kill；持续失败保留 PID 所有权和停止信号供重试，不覆盖原始异常；Popen 失败关闭日志句柄。
- E-10：桌面关闭状态探测和等待在后台线程完成；确认后销毁窗口，GUI 线程不轮询阻塞。
- E-11：日志限额/轮换，默认关闭高频 access log。
- E-12：停止文件必须是 32 位随机 nonce 文件名且内容匹配，保留原有目录逃逸检查。
- E-13–16 / 20：移除 Codex 路径优先分支；CMD 支持执行策略包装与参数转发，只有错误才 pause；pythonw 缺失时 PS/VBS 均回退隐藏 python.exe；版本确认先于 venv 创建。
- E-17：检测实际 renderer；MSHTML 不加载主界面，显示 WebView2 安装说明。
- E-18：新增 stop-web.cmd / 受鉴权停止接口；活动操作或未保存数据时拒绝停止；隐藏服务监视父进程退出，走正常关停。
- E-19：复用校验 checkout/build 指纹及协议，不再只信 OpenAPI title。
- E-21：--no-browser / --stop 控制台错误不弹 MessageBox，错误日志同样有上限。
- T-01 / T-02：采集测试隔离 KERR_STATE_DIR，验证连续运行两次不污染用户状态；响应样本原子写入，Python / Node 可用 KERR_FIXTURE_PATH 分离并行流水线。

## 保留的限制与验证边界

1. **Windows 持续外部读锁**：若其他程序一直持有不允许删除共享的文件句柄，操作系统不允许原子替换。此时本次修复采取“有限重试后明确拒绝、旧标定保持有效”，**不承诺在外部句柄尚未释放时仍安装成功**。这比原来的裸 PermissionError 更可控，专项用例覆盖了重试成功和持续占用两条路径。
2. 原报告 CAL-4–10 已明确列为设计限制：端点/平顶/混叠弱峰检测、模型类型宽松性和极窄覆盖外推阈值，本轮没有擅自换找峰算法或宣称消除这些限制。饱和 ROI 仍拒绝用于正式拟合。
3. 没有通过真实 GUI 验证窗口激活、WebView2 缺失界面或关闭对话框外观；这些通过源码核对和替身数据流检查。没有实测真实仪器，也没有进行断电恢复测试。
4. 部分 API 错误码现已按报告改为 404/422；已更新原有测试中的对应期望和测试替身签名，但依用户要求没有运行原有完整套件。旧测试报告中的全部通过记录不能当成本版全量验证结果。

## 运行与结果

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_reported_bugs.py --junitxml=test-output/reported-bugs-python.xml
node --experimental-vm-modules tests/reported-bugs.cjs
```

- Python：68 passed，0 failed / xfailed / skipped；其中包含本测试自己创建的真实 worker 失效、两个隔离 fixture 重启，以及真实 PowerShell 的探测和参数回退。进程启动动作使用替身，不创建 App 窗口。
- 前端：17 checks passed；Node VM 执行真实前端模块，复用用户 QA 的最小 DOM 驱动，输入为现有真实模拟响应样本，只检查报告中的问题。
- 报告：`test-output/reported-bugs-python.xml`、`test-output/reported-bugs-frontend.json`。
- Python 测试客户端有一条弃用提示；Node VM Modules 有一条实验特性提示，均未导致失败。

重新加载前端并重启原本地后端后生效。已有服务若属于旧版本，应先从所属控制台或窗口正常停止，避免在采集或写盘时强杀。
