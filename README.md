# Kerr Studio · 原生 Qt 桌面版

以 PySide6 原生控件和 pyqtgraph 绘图替换原来的网页前端，沿用 Python 采集、仪器驱动、HDF5、拟合和标定后端。三个工作页为 Acquisition、Analysis 和 Spectral Calibration。

## 启动

Windows 10/11 64 位，使用 Python 3.12 或更新的 64 位版本。完整解压本文件夹后双击 **Kerr studio.exe**；也可双击 `start-app.vbs` 或 `start-app.cmd`。

第一次启动会在当前软件文件夹创建 `.venv` 并下载依赖，安装窗口显示四步进度、下载量及等待状态，完成后打开软件。之后通过 `pythonw.exe` 启动原生 Qt 窗口。窗口先显示工作台与加载遮罩，后台采集进程就绪后解除遮罩。

依赖环境位于本文件夹的 `.venv`，运行日志和默认输出文件位于 `data`。安装需要网络，不需要修改系统 Python、安全策略或注册表。若下载失败，可重新启动；日志为 `data/setup.log`、`data/setup-pip.log`，当前进度为 `data/setup-progress.json`。

## 仪器与数据

- 默认硬件模式；未连接时可进入软件，已连接的仪器独立可用，其余对应操作置灰。顶栏模式按钮可切换 Simulation。
- Delay Stage 状态按钮配置 PI C-663.12 / M-414.32S，通过原生 USB 或串口连接。真实设备需安装 PI 驱动与 64 位 GCS2 库；软件不自动激活电机或执行回零。
- Detection 状态按钮选择 Andor Newton DU920P（USB / SDK2）以及光谱仪。CCD 需另外安装官方 64 位 SDK2 与设备驱动。Oriel 77400 当前使用手动设置，预留其他光谱仪驱动接口。
- Acquisition 为采集工作台；文件名和保存文件夹在底栏设置。有覆盖风险时确认覆盖或取消；确认覆盖后直接替换，不保留旧文件备份。
- Analysis 打开的 HDF5 独立于当前采集，支持指定波长的衰减与拟合，以及指定延迟的光谱叠加。分析按每帧曝光时间归一化。
- Spectral Calibration 支持全图/FVB、选行提取、参考线及多项式标定。模拟参考不能用于真实 CCD 标定；安装标定只影响后续采集。
- 关闭时等待采集停止和 HDF5 写入完成。实验运行中请通过软件停止并关闭窗口。

新增光谱仪在 `backend/drivers/registry.py` 注册型号、支持的连接方式和可调参数，复用当前设备接口。真实硬件连接需要在对应仪器上另行确认。

## 原生界面与启动组件

应用通过 Qt 信号和后台工作进程通信，无需浏览器、本地 HTTP 服务、WebView 或 Python.NET。源目录只包含原生界面、后端、图标和启动/依赖文件，没有旧的 HTML、CSS、JavaScript 或 WebView 加载配置。

`Kerr studio.exe` 是带图标的轻量启动器，使用 Windows 的 .NET Framework 4.x 启动 `start-app.vbs`；Qt/Python 进程不加载 CLR。启动器源码为 `launcher.cs`，图标为 `assets/TRPL.ico`。

## 不打开界面的检查入口

在本目录运行 PowerShell：

```powershell
.\start-app.ps1 -SetupOnly # 安装/检查依赖，不启动窗口
.\start-app.ps1 -Probe     # 已安装环境的依赖检查
& ".\Kerr studio.exe" --check        # 检查必要程序文件，不启动窗口
```

后台进程首次启动不加载 SciPy 拟合/标定计算模块，调用相关功能时再加载；界面使用增量记录与绘图节流。开发验收脚本保留在工作区 `.publish`，不随精简软件分发。

## 重建启动器

在本目录运行：

```powershell
& "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:winexe /out:"Kerr studio.exe" /win32icon:assets\TRPL.ico /reference:System.Windows.Forms.dll launcher.cs
```
