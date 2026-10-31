# Kerr-gated TRPL Spectroscopy · Kerr Studio

用于光学 Kerr 门时间分辨光致发光（TRPL）的 Python 控制与分析软件，提供独立 Windows 桌面窗口和浏览器工作台。

**当前版本 0.1.1 为模拟采集版本。真实仪器尚未联机验证，硬件采集模式没有开放。** 所有模拟信号、运动反馈、温度及校准全图均有模拟来源标记。

## 已实现

- **Acquisition**：实时光谱、非均匀延迟二维图、Single / Scan / Repeat、分段扫描、每点多帧、暂停/继续与停止。
- **存储**：原始 int32 光谱、逐帧提交记录、HDF5 自动保存/内存保存、重开实验与 CSV 导出。
- **Analysis**：按实验保存的波长轴提取原始计数/曝光曲线；单/双指数与用户给定高斯 IRF 卷积拟合、时间范围、参数近似误差、残差及 CSV。
- **Spectral Calibration**：后端模拟 1024×255 CCD 全图、ROI 行均值提谱、亚像素找峰、参考 CSV、人工谱线配对、1–3 阶拟合、标定应用/导出与持久化。
- **桌面入口**：pywebview + WebView2 独立窗口；先显示加载页，再后台启动/复用本机后端。
- **互斥操作**：Analysis 顶栏正常工作；Spectral Calibration 在采集时锁定校准操作，空闲时锁定顶栏采集操作，暂停和收尾仍视为忙碌。

## 目标硬件

| 部件 | 型号/连接 | 当前状态 |
|---|---|---|
| CCD | Andor Newton DU920P，1024 光谱像素 | 模拟 FVB；预留 pylablib → 官方 SDK2 适配入口，未联机验收 |
| 延迟台 | PI M-414.32S，USB | 模拟控制；仍需确认配套控制器型号、轴号、参考状态及安装限位 |
| 光谱仪 | Oriel 77400 / MS125 | 手动设置记录与波长标定；不发送光谱仪调节命令 |

双程光路采用 $\Delta t=2\Delta x/c$，1 ps 对应约 0.149896229 mm 位移。延迟正负号、光学零点和真实运动限位需要在实际装置上确认。

真实连接请求会明确报错，不会静默回退成模拟设备。模拟运行不需要 Andor SDK、pylablib 或 PI DLL；仓库不分发厂商 SDK。

## Windows 安装与启动

需要 **64 位 Python 3.12+**。桌面模式还需要 Windows WebView2 Runtime。

~~~powershell
git clone https://github.com/ProphetZ-star/Kerr-gated_TRPL_spectroscopy.git
cd Kerr-gated_TRPL_spectroscopy
.\start-app.cmd
~~~

第一次启动会探测 64 位 Python 3.12+（py -3.12、py -3、python、python3），创建项目 .venv 并安装 requirements-desktop.txt。双击 **start-app.vbs** 会先检查 Python 版本/位数及桌面依赖；环境缺失或依赖不完整时，自动打开 start-app.cmd 显示安装进度，准备完成后启动独立窗口。环境完整时直接隐藏启动。

如果旧下载副本提示 `No module named 'webview'`，请先运行一次 **start-app.cmd** 补装 requirements-desktop.txt，再使用 VBS。网页版 requirements.txt 不包含桌面依赖；GitHub 不分发 .venv，依赖需在本机安装。

仅准备环境而不打开窗口：

~~~powershell
.\start-app.ps1 -SetupOnly
~~~

### 浏览器模式

首次运行 **start-sim.cmd** 准备环境并打开网页；环境准备好后，可双击 **start-web.vbs**。本机地址为 http://127.0.0.1:8765 ，服务就绪后自动打开。不要直接双击 index.html，也不要在后端未运行时只输入网址。

~~~powershell
# 有控制台的入口；自己启动的服务可用 Ctrl+C 正常停止
.\start-sim.ps1

# 只启动服务
.\start-sim.ps1 -NoBrowser

# 指定端口
.\start-sim.ps1 -Port 8766
~~~

已有同项目、同代码版本服务时会复用；其他程序或旧版服务占用端口时会报错。升级前请从原窗口或控制台正常停止旧服务。隐藏网页入口自己启动的后端会保持运行，关闭标签页不会停止采集。App 若自行启动后端，退出时会确认活动操作和未保存数据，并关闭它拥有的后端；复用其他入口的服务时不会关闭该服务。

关闭隐藏网页服务可运行 **stop-web.cmd** 或 `start-sim.cmd -Stop -NoBrowser`；若有活动采集或未保存实验，会拒绝关闭，请先停止并保存/丢弃数据。

Windows 虚拟环境启动器与实际 Python 进程可以有不同 PID；现在通过每次启动的独立 launch_id 识别所属服务，避免误关后端造成 127.0.0.1 拒绝连接。launch_id 不用于认证。

也可手动安装、运行：

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m backend
~~~

其他平台可运行 Python 后端和浏览器界面；此仓库的启动脚本及桌面宿主以 Windows 为目标。

## 采集与数据

1. 预览信号，设置曝光、延迟扫描分段及 Scan / Single / Repeat。
2. 点击 Start；Pause 在完整点提交后暂停，Stop 保留已提交的完整帧。
3. Autosave 默认开启；路径为空时写入 data/ 内唯一 HDF5 文件。内存采集结束后可在 Analysis 中 Save HDF5。
4. Analysis 可通过本机绝对文件路径重开本程序生成的实验。
5. 默认每轮最多 5001 点、10 个分段，总计最多 25000 帧；内存模式另设 128 MiB 原始记录估算限额。

HDF5 保留 /raw/spectra、/raw/record_index、/raw/record_meta、/raw/committed，以及设备、标定、背景、坐标、模拟来源和分析元数据。采集中使用 .partial.h5，正常结束或停止后完成终态命名；异常文件保留供恢复。CSV 导出每个已采单帧的窗口像素平均原始计数，不随显示背景扣除变化。

已完成帧的 flush 不等于突然断电时必然可恢复。原始实验数据、日志、WebView 缓存、虚拟环境和本地标定安装文件均被 Git 忽略。

## 波长标定与 Analysis

校准页的图像、提谱、找峰和拟合均来自后端；前端负责显示与人工配对。参考 CSV 为 wavelength_nm,relative_intensity（强度列可省略），示例谱线不是灯谱数据库。支持递增/递减轴；拟合检查重复配对、秩、病态条件数和整个连续 CCD 区间的折返，并提示外推区域。air/vacuum 仅记录约定，不自动换算。

Apply 按服务端 solution_id 安装已验证结果，只用于未来采集。**旧实验始终使用采集时保存的波长轴**，不会被当前标定追溯修改。安装标定与手动光谱仪记录保存在 data/installed-calibration.json；原始校准图像和未应用草稿仅在内存缓存中。模拟帧产生的标定始终标为 synthetic。

Analysis 使用所有帧和轮次的原始计数/曝光；显示扣背景和 Acquisition 的显示轮次筛选不改变拟合输入。IRF 是用户指定高斯宽度，参数误差为局部 Jacobian 近似，不是严格置信区间。IRF FWHM 范围为 0.0001–100 ps。拟合支持协作取消，最多 4 个运行/排队任务，内存保留最近 64 个结果；已写入 HDF5 的结果全部保留。尚未支持实测 IRF、bootstrap 或自定义坏点剔除。

## 无界面检查

先安装开发与桌面依赖（桌面测试使用 mock，不打开窗口）：

~~~powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -r requirements-desktop.txt
.\.venv\Scripts\python.exe -m pytest -q tests/test_workbench_flow.py tests/test_desktop.py --junitxml=test-output/workbench-python.xml

# Python 生成真实后端响应样本后，用 Node.js 执行前端模块契约检查
node --experimental-vm-modules tests/workbench-flow.cjs
~~~

上述为可选开发检查命令，先前的 15 项 Python / 24 项前端通过记录属于旧版，不能作为本版全量验收。本版按 BUG 报告仅做专项回归：68 项 Python、17 项前端检查通过；随后启动连接修复的 7 项相关检查通过（其中 4 项为新增启动回归，3 项复用报告用例）。没有打开浏览器或 App 窗口，也未连接真实仪器。

专项回归复现（Windows、Node.js）：

~~~powershell
# 先停止当前 checkout 的服务，避免项目实例锁冲突
.\.venv\Scripts\python.exe -m pytest -q tests/test_reported_bugs.py tests/test_launcher_connection.py
# 只生成前端检查所需的合成接口响应样本
.\.venv\Scripts\python.exe -m pytest -q tests/test_workbench_flow.py::test_calibration_analysis_hdf5_roundtrip
node --experimental-vm-modules tests/reported-bugs.cjs
~~~

样本默认写入 test-output/data-flow-fixtures.json。独立验证流水线可设置 KERR_FIXTURE_PATH 为各自路径，Python 和 Node 应使用相同值。实验数据、生成样本与测试输出不随源码发布。

桌面依赖自检另有 6 项针对性回归通过（`python -m pytest -q tests/test_desktop_setup.py`）；使用 Windows 脚本宿主和启动替身验证分支，未打开 GUI 或执行真实安装。

可选浏览器检查脚本需要另外安装 playwright 和 Edge；常规数据流检查不需要这些浏览器依赖。早期原型截图和旧 UI-only 校准检查未作为当前实现的验收结果发布。

## 项目结构

- backend/：FastAPI、进程通信、采集/拟合、校准和 HDF5。
- frontend/：工作台界面、状态、图表、分析与校准交互。
- desktop.py / desktop_service.py：桌面宿主及本地服务生命周期。
- browser.py / start-*：网页和桌面入口。
- tests/：Python 接口/存储测试、Node 前端模块检查与可选浏览器检查。
- [BUG 修复与专项回归](docs/BUG修复与专项回归.md)：按原报告编号记录修复与边界。
- [启动连接修复](docs/启动连接修复.md)：Windows PID 归属问题与 HTTP 回归。
- [数据流检查](docs/数据流检查.md)：接口、元数据和验证边界。
- [原项目说明](docs/PROJECT_OVERVIEW.md)：仓库原有的项目背景与初始规划。
- docs/design/：历史设计记录，功能状态以当前 README 为准。

后端默认仅监听本机；写入接口使用会话 token，拒绝跨源请求，并通过实例锁避免重复占用设备。API 文档位于运行后的 /docs。日志位于 data/desktop-service.log、data/desktop-error.log 和 data/browser-error.log。

DeepSeek 参与过部分界面及校准计算初稿；主模型完成集成、数据流修正和验证。许可证尚未选择，未添加授权条款。
