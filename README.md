# Kerr Studio · 原生 Qt 桌面版

以 PySide6 原生控件和 pyqtgraph 绘图替换原来的网页前端，沿用 Python 采集、仪器驱动、HDF5、拟合和标定后端。三个工作页为 Acquisition、Analysis 和 Spectral Calibration。

## 启动

Windows 10/11 64 位，使用 Python 3.12 或更新的 64 位版本。完整解压本文件夹后双击 **Kerr studio.exe**；也可双击 `start-app.vbs` 或 `start-app.cmd`。

第一次启动会在当前软件文件夹创建 `.venv` 并下载依赖，安装窗口显示四步进度、下载量及等待状态，完成后打开软件。之后通过 `pythonw.exe` 启动原生 Qt 窗口。窗口先显示工作台与加载遮罩，后台采集进程就绪后解除遮罩。

完整依赖检查成功后会缓存校验结果；后续启动先检查 Python、依赖版本和依赖文件是否变化，未变化时跳过重复导入。缓存失效或上次启动失败时回退完整检查，`-Probe` 始终执行完整检查。初始只创建 Acquisition 页面，Analysis 和 Spectral Calibration 在首次切换时创建并同步当前状态。

无变化的光谱不重复绘制；状态轮询在前台空闲时为 1 秒、采集时为 150 毫秒，最小化后分别为 3 秒和 500 毫秒。操作请求和恢复窗口会立即唤醒状态同步；最小化仅暂停前端绘图，不暂停后台采集或保存。

依赖环境位于本文件夹的 `.venv`，运行日志和默认输出文件位于 `data`。安装需要网络，不需要修改系统 Python、安全策略或注册表。若下载失败，可重新启动；日志为 `data/setup.log`、`data/setup-pip.log`，当前进度为 `data/setup-progress.json`。

## 仪器与数据

- 默认硬件模式；未连接时可进入软件，已连接的仪器独立可用，其余对应操作置灰。顶栏模式按钮可切换 Simulation。
- Delay Stage 状态按钮配置 PI C-663.12 / M-414.32S，通过原生 USB 或串口连接。真实设备需安装 PI 驱动与 64 位 GCS2 库；软件不自动激活电机或执行回零。
- Detection 状态按钮选择 Andor Newton DU920P（USB / SDK2）以及光谱仪。CCD 需另外安装官方 64 位 SDK2 与设备驱动。Oriel 77400 当前使用手动设置，预留其他光谱仪驱动接口。
- Acquisition 为采集工作台；文件名和保存文件夹在底栏设置。有覆盖风险时确认覆盖或取消；确认覆盖后直接替换，不保留旧文件备份。
- Analysis 打开的 HDF5 独立于当前采集，支持指定波长的衰减与拟合，以及指定延迟的光谱叠加。分析按每帧曝光时间归一化。
- 自动保存和历史 HDF5 使用按需分块读取，每个实验的原始帧页缓存上限为 8 MiB；拟合按块汇总，保留完整数据和跨分块的饱和点剔除规则。Memory-only 仍保留全部帧，手动保存成功后切换到磁盘读取。此上限不包括前端地图、拟合汇总结果、临时读块和 CSV 输出文本。
- 已打开的实验依赖原 HDF5 文件，请在关闭该实验后再移动或替换文件。失败采集的文件自动移入回收站前，会先保留一份会话临时副本供分析和另存；若副本无法创建，则保留原文件。
- Spectral Calibration 支持全图/FVB、选行提取、参考线及多项式标定。模拟参考不能用于真实 CCD 标定；安装标定只影响后续采集。
- 关闭时等待采集停止和 HDF5 写入完成。实验运行中请通过软件停止并关闭窗口。

新增光谱仪在 `backend/drivers/registry.py` 注册型号、支持的连接方式和可调参数，复用当前设备接口。真实硬件连接需要在对应仪器上另行确认。

## 原生界面与启动组件

应用通过 Qt 信号和后台工作进程通信，无需浏览器、本地 HTTP 服务、WebView 或 Python.NET。源目录只包含原生界面、后端、图标和启动/依赖文件，没有旧的 HTML、CSS、JavaScript 或 WebView 加载配置。

`Kerr studio.exe` 是带图标的轻量启动器，使用 Windows 的 .NET Framework 4.x 启动 `start-app.vbs`；Qt/Python 进程不加载 CLR。启动器源码为 `launcher.cs`，图标为 `assets/Kerr studio.ico`，矢量源图为 `assets/Kerr studio.svg`。

## 不打开界面的检查入口

在本目录运行 PowerShell：

```powershell
.\start-app.ps1 -SetupOnly # 安装/检查依赖，不启动窗口
.\start-app.ps1 -Probe     # 已安装环境的依赖检查
& ".\Kerr studio.exe" --check        # 检查必要程序文件，不启动窗口
```

运行依赖只有 NumPy、h5py、pydantic、PySide6-Essentials、pyqtgraph 以及 PI 位移台所需的 PIPython/pyserial/pyusb；寿命拟合与 IRF 卷积由 `backend/numerics.py` 用 NumPy 实现，不需要 SciPy。界面使用增量记录与绘图节流。开发验收脚本保留在工作区 `.publish`，不随精简软件分发。

## 重建启动器

在本目录运行：

```powershell
& "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:winexe /out:"Kerr studio.exe" /win32icon:"assets\Kerr studio.ico" /reference:System.Windows.Forms.dll launcher.cs
```
