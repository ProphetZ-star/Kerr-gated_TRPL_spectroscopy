# TRPL · Kerr Studio Desktop

Windows 桌面精简版 Kerr 门时间分辨荧光采集与分析工作台。独立窗口运行，无浏览器地址栏；默认进入硬件模式；未连接仪器也可打开主界面，并可手动切换为模拟模式。

## 启动

1. 下载并完整解压本仓库，放到可写的文件夹中。
2. 准备 **64 位 Python 3.12 或更新版本**、**Microsoft Edge WebView2 Runtime** 和 **.NET Framework 4.8**。
3. 双击带图标的 **`TRPL.exe`**。首次启动显示 4 个阶段：检查环境、创建环境、下载/安装依赖、验证依赖；通过后打开软件。下载时显示当前依赖的 MB、百分比与速度，安装期间每 8 秒更新一次等待状态。百分比是当前依赖文件的下载进度，不代表整个安装进度。

`TRPL.exe` 是带图标的轻量启动器，不是捆绑 Python 的独立安装包。首次安装需要联网；Python 依赖保存在软件目录的 `.venv` 中，之后复用该环境。整个软件文件夹移动后，可继续从新位置启动；如现有虚拟环境因移动失效，删除该目录下的 `.venv`，下次启动会重建。

桌面加载兼容处理由 `desktop_runtime.py` 和 `desktop-runtime.config` 提供，复制软件时必须一起保留。安装时会在 `.venv/DesktopRuntime` 中准备软件专用的 Python 启动进程及相邻配置，继续复用 `.venv` 中的依赖。该配置只允许本软件进程加载 Windows 判定为远程来源的桌面组件，不改系统 Python、注册表或全局 .NET 安全设置。

安装验证、双击快速检查和正常桌面启动使用同一套加载方式。已有依赖完整时重试可直接验证，不需要全部重新下载；日志会逐项显示检查的模块。缺少这两个新增文件时会明确提示重新复制完整软件包。

也可双击 `start-app.vbs` 启动。仅准备环境时，在软件目录运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-app.ps1 -SetupOnly
```

可为 `TRPL.exe` 创建桌面快捷方式；快捷方式会沿用 TRPL 图标。启动器自身的只读文件检查命令为 `TRPL.exe --check`，不会打开软件或安装依赖。

## 硬件连接

默认进入硬件模式，允许仪器全部断开。点击 Delay Stage 标题右侧的状态按钮，选择 **PI C-663.12 · M-414.32S**：

- USB（默认）：安装 PI Software Suite 的 USB 驱动和 64 位 GCS2 DLL，点 **Find PI USB**，选择发现的控制器。通常可自动定位 DLL，也可在框中填写 PI GCS2 DLL 的完整路径。
- RS-232 / COM：选择串口及与控制器拨码一致的波特率。当前适配器支持 9600、19200、38400、115200；不能仅根据软件下拉框假定控制器的设置。
- 连接时核对 C-663.12 型号、`M-414.32S` 台体配置、轴、运动范围、电机启动状态和参考状态。请先在 PIMikroMove 中配置并完成参考；本软件不自动启用电机、找参考或改变运动参数。
- M-414.32S 没有编码器，`POS?` 在此配置下报告步进轮廓发生器的位置。界面标为 **Step count**，数据标记为 `controller_step_counter`，不作为独立传感器实测位置。扫描按控制器读回的软限位检查，包括参考后的负坐标。

点击 Detection 标题右侧的状态按钮，分别设置 CCD 与光谱仪：

- **CCD**：Andor Newton DU920P，通过 USB / 官方 SDK2 选择相机，不使用 COM 口。需安装 Andor 的 64 位 SDK2 和相机驱动，再选择 `atmcd64d.dll` 所在文件夹。
- **Oriel 77400 / MS125**：默认手动模式，不需要串口或控制器。先手动调好仪器，再在软件中记录中心波长、光栅及狭缝；记录值不会发送到仪器，状态显示 **Manual**，不显示虚假的“已连接”。
- **其他手动光谱仪**：可选 Other spectrometer · Manual 并记录型号。
- **未来自动控制光谱仪**：保留 Serial、USB、TCP/IP、VISA 连接信息。未安装具体型号驱动时禁用 Connect，不猜测协议或发送通用命令。添加驱动的方法见 [INSTRUMENT_DRIVERS.md](INSTRUMENT_DRIVERS.md)。

各设备独立启用：仅 CCD 即可预览、采背景及标定帧；仅位移台即可移动及设置 t₀；TRPL 扫描需要 CCD 和位移台。手动光谱仪记录、Analysis 文件处理、标定文件导入不要求电子连接。

硬件与模拟数据、t₀ 和标定分开。更换光谱仪型号或修改光学设置会清除当前波长标定；自动控制驱动必须回读实际设置。没有实测标定时显示 pixel，不用合成波长冒充实测标定。真实硬件尚未经过实机验证。

接口依据：[C-663.12 官方规格](https://www.physikinstrumente.com/en/products/controllers-and-drivers/motion-controllers-drivers-for-linear-torque-stepper-dc-servo-motors/c-66312-mercury-step-stepper-motor-controller-900553/)、[M-414 官方规格](https://www.physikinstrumente.de/de/produkte/lineartische/tische-mit-dc-buerstenlosen-dc-bldc-schrittmotoren/m-414-praeziser-hochlast-lineartisch-701755/)、[PI 官方 PIPython](https://github.com/PI-PhysikInstrumente/PIPython)。

## 使用

- **Acquisition**：实时预览、位移台调节、分段扫描、背景采集、暂停/恢复与 HDF5 保存。确认覆盖时直接替换同名实验文件，不生成备份。
- **Analysis**：`Decay & fit` 查看指定波长的衰减并拟合；`Spectrum` 选择多个 delay 叠加波长光谱，颜色按时间先后映射。导入文件只影响 Analysis。
- **Spectral Calibration**：CCD Full image / FVB 采集（按当前硬件或模拟模式）、选行提取、谱线指认、波长标定拟合及 JSON 导入/导出。
- 显示轴的最小值/最大值可点击原位编辑，Enter 应用，Esc 取消。

实验与运行日志默认位于 `data`，可另选实验保存目录。程序采用有界异步批量写入和前端增量更新；状态日常增量同步，每 60 秒完整同步。退出时仍会确认未保存或正在采集的数据。

安装日志保存在 `data/setup.log`，下载详情在 `data/setup-pip.log`，当前阶段在 `data/setup-progress.json`。安装器长时间无输出会报错，不会无限静默等待；同一文件夹同时只允许一个安装器运行。

遇到启动问题，可查看 `data/desktop-error.log` 与 `data/desktop-service.log`。默认本地端口为 8765；退出另一份旧程序后再启动，避免多个目录的服务占用同一端口。

## 文件说明

仓库只保留桌面启动链、内嵌界面、采集/分析后端、依赖清单和图标。`index.html` 与 `frontend` 是桌面窗口必需的内嵌界面资源，不能单独删除。独立网页入口、浏览器启动器、测试、开发工具、历史设计资料、实验文件和本地虚拟环境均不随此版本分发。

`launcher.cs` 是 `TRPL.exe` 的完整源码，文件开头附重新编译命令。程序运行不需要安装 C# 开发工具。
