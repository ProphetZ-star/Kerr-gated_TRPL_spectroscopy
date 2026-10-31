# Kerr-gated TRPL Spectroscopy · Kerr Studio

用于光学 Kerr 门时间分辨光致发光（TRPL）的 Python 控制与分析软件，提供 Windows 独立桌面窗口及浏览器工作台。当前程序版本为 0.1.1，本次源码更新日期为 2026-11-01。

**当前采集模式为模拟。真实硬件尚未完成适配和联机验证。** 模式按钮提供 Simulation / Real hardware；选择真实硬件时后端会明确提示未完成适配，不会将模拟数据显示为实测。

## 功能与目标硬件

- Acquisition：实时光谱、TRPL 热图与衰减曲线、分段扫描、Single / Scan / Repeat、每点多帧、暂停/继续、停止、背景采集。
- 存储：逐帧提交的 int32 原始光谱、HDF5 自动保存/内存保存、实验重开与 CSV 导出。
- Analysis：按实验记录的波长轴提取计数/曝光曲线，单/双指数与用户给定高斯 IRF 卷积拟合、时间窗、残差、局部参数误差、取消拟合及结果保存。
- Spectral Calibration：后端模拟 1024×255 CCD 全图、ROI 行均值提谱、找峰、参考 CSV、人工谱线配对、1–3 阶映射拟合、应用/导出与持久化。
- 图表：九张图均可通过 Axes… 编辑坐标范围或恢复 Auto；共享坐标联动，缩放不改变采集数据或拟合输入。CCD 保留图像及 ROI，无网格；Live Spectrum / Decay 显示主网格。

| 部件 | 目标配置 | 当前边界 |
|---|---|---|
| CCD | Andor Newton DU920P，1024 光谱像素 | 模拟 FVB；预留 pylablib → 官方 SDK2 适配入口，未联机 |
| 延迟台 | PI M-414.32S，USB，双程光路 | 模拟运动；配套控制器型号、轴号、参考状态及安装限位待确认 |
| 光谱仪 | Oriel 77400 / MS125 | 手动设置记录和波长标定，不发送光谱仪调节命令 |

双程光路采用 $\Delta t=2\Delta x/c$，1 ps 对应约 0.149896229 mm。真实装置仍需确认延迟符号和光学零点。校准页全图是独立模拟功能，不能代表真实 CCD 任意 ROI 或 Image 模式已经开放。仓库不分发厂商 SDK；模拟运行无需 SDK2、pylablib 或 PI DLL。

## Windows 安装与启动

需要 **64 位 Python 3.12+**；桌面模式还需要 Microsoft Edge WebView2 Runtime。

```powershell
git clone https://github.com/ProphetZ-star/Kerr-gated_TRPL_spectroscopy.git
cd Kerr-gated_TRPL_spectroscopy
.\start-app.cmd
```

首次启动会探测 Python、在项目目录创建 `.venv`，并安装 `requirements-desktop.txt`。之后双击 **start-app.vbs** 即可；VBS 会检查环境及桌面依赖，缺失时自动打开安装流程，环境完整时隐藏启动。Python 位于 `.venv/Scripts/python.exe`，依赖位于 `.venv/Lib/site-packages`。GitHub 下载不包含虚拟环境；出现 `No module named 'webview'` 时先运行一次 start-app.cmd 补装桌面依赖。

```powershell
# 仅准备环境，不打开窗口
.\start-app.cmd -SetupOnly

# 浏览器模式：准备后端依赖并打开网页
.\start-sim.cmd

# 只启动本地服务，或指定端口
.\start-sim.cmd -NoBrowser
.\start-sim.cmd -Port 8766

# 正常停止空闲且已处理未保存数据的网页服务
.\stop-web.cmd
```

环境准备后可双击 **start-web.vbs**。默认地址为 http://127.0.0.1:8765；不要直接打开 index.html。App 启动先显示禁用的灰色工作台和旋转等待图标，后端就绪后进入主界面。

仅同目录、同代码版本的服务可以复用。其他目录/旧版本占用端口时会报错，升级前请从所属窗口或控制台正常停止旧服务。Windows 虚拟环境启动器可能与实际 Python 进程具有不同 PID，程序使用独立启动标识识别归属。

App 自己启动的服务沿用退出确认和数据保护流程；复用的服务仅在完全空闲、无已加载实验/背景/校准草稿/运行中拟合时自动停止。关闭浏览器标签不会停止采集。日志位于 data/desktop-service.log、data/desktop-error.log、data/browser-error.log；运行后的 `/docs` 提供 API 文档。

## 采集与保存

1. 在 File name 输入文件名，默认 Sample 01 TRPL，自动补 `.h5`；Choose folder 在 App 打开系统文件夹选择器，网页入口提供目录路径输入，默认项目 data 目录。
2. 在 Acquisition 标题右侧启动/停止 Live preview。设置曝光和扫描分段后 Start；暂停在完整点提交后生效，停止保留已提交帧。Set zero t₀ 重定义零点；Go to t₀ 仅移动回当前零点。
3. 同名 `.h5`、`.partial.h5`、`.stopped.h5` 或 `.events.jsonl` 存在时，弹窗选择覆盖或取消。覆盖确认绑定到当前文件状态，变化后须重新确认；旧文件保存在同目录 `.<文件名>.overwrite-backup-*` 备份文件夹，不自动删除。
4. 新采集成功建立后默认替换旧 memory-only 数据。取消覆盖、参数错误或新文件创建失败不会提前丢弃旧记录；需要保存旧记录时应先使用 Analysis 的 Save HDF5。
5. Analysis 的 Open… 在 App 中选择本地 HDF5；网页模式选择文件后导入副本到 data/imports，单文件上限 256 MiB，原文件不改写。

每轮最多 5001 点、10 个分段、重复最多 100 轮，每次运行总计最多 25000 帧；内存模式另设 128 MiB 原始记录估算限额。HDF5 保存原始光谱、记录索引、逐帧提交标志及设备、背景、标定、坐标、模拟来源和分析元数据；采集中使用 `.partial.h5`。已完成帧的 flush 不等于突然断电时必然可恢复。

Acquisition 默认汇总所有轮次且不扣背景。Additional settings 已移除；波长窗口通过 Analysis 控件调整，实时谱图仍支持点击选波长。模拟故障注入记录不会从旧实验自动带入新扫描。

## Analysis、标定与显示范围

旧实验始终使用采集时记录的波长轴。Analysis 使用所有原始帧及轮次，按曝光归一化；显示范围不裁剪拟合输入，拟合时间窗须在 Fit controls 设置。IRF 为用户指定高斯宽度，FWHM 0.0001–100 ps；参数误差为局部 Jacobian 近似，不是严格置信区间。最多 4 个运行/排队拟合，内存保留最近 64 个结果；HDF5 保留全部已写入结果。尚不支持实测 IRF、bootstrap 或自定义坏点剔除。

校准参考 CSV 为 `wavelength_nm,relative_intensity`，强度列可省略。示例谱线不是灯谱数据库。支持递增/递减轴；检查重复配对、秩、病态条件数、全 CCD 范围折返，并提示外推。air/vacuum 仅记录约定，不自动换算。Apply 按服务端 solution_id 安装，只用于未来采集；安装标定与光谱仪记录保存在 data/installed-calibration.json，图像和未应用草稿仅在内存。

每张图右上角 **Axes…** 可分别关闭 Auto X / Auto Y 并修改最小、最大值，Reset to auto 恢复自动。手动范围仅在当前前端会话保留；对数图输入图上显示的 log₁₀ 坐标值。Map/Decay 延迟轴、Analysis 主图/残差延迟轴、标定映射/残差像素轴分别联动；线性/对数与像素/nm 设置分开保存。Analysis 顶栏正常使用；校准页忙碌时锁定校准操作，空闲时锁定顶栏采集操作。

## 开发与专项验证

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -r requirements-desktop.txt

# 生成合成接口响应样本，供 Node 前端检查使用
.\.venv\Scripts\python.exe -m pytest -q tests/test_workbench_flow.py::test_calibration_analysis_hdf5_roundtrip
node --experimental-vm-modules tests/plot-ranges.cjs
node --experimental-vm-modules tests/save-workflow.cjs
node --experimental-vm-modules tests/ui-usability.cjs
node --experimental-vm-modules tests/reported-bugs.cjs

# 保存、文件选择、模板及桌面逻辑专项
.\.venv\Scripts\python.exe -m pytest -q tests/test_save_workflow.py tests/test_ui_usability.py tests/test_desktop.py
```

测试需要独占当前 checkout 的实例锁，先正常停止该目录的服务。接口响应样本默认写入 test-output/data-flow-fixtures.json；并行流水线可分别设置 KERR_FIXTURE_PATH，Python 与 Node 应使用同一值。可选浏览器检查另外需要 Playwright 和 Edge。

最新图表版本的专项记录为 **51 项前端检查及 1 项启动模板检查通过**；上一轮保存流程的 23 项 Python 检查、此前退出清理的 27 项检查分别见文档。它们来自分阶段验证，不能相加视为一次全量通过。此次发布未重新运行整套验收，也未打开软件窗口、做截图验收或连接真实硬件。

## 文档

- [最新图表范围与版面](docs/图表范围与版面调整.md)
- [保存工作流与坐标轴](docs/工作流与坐标轴调整.md)
- [启动及界面易用性](docs/界面易用性修改.md)
- [空闲服务退出](docs/空闲服务退出修复.md)
- [BUG 修复与专项回归](docs/BUG修复与专项回归.md)
- [Windows 启动连接修复](docs/启动连接修复.md)
- [数据流检查](docs/数据流检查.md)
- [原项目背景](docs/PROJECT_OVERVIEW.md)；docs/design 为历史设计记录，功能状态以本 README 和最新变更说明为准。

本地服务默认仅监听回环地址，写操作要求会话 token，拒绝跨源请求；实例锁避免同目录重复占用设备。实验数据、日志、缓存、虚拟环境、SDK 和备份不随源码发布。

DeepSeek 参与过部分界面及校准计算初稿，主模型完成后续集成、修正和验证。许可证尚未选择，仓库未新增授权条款。
