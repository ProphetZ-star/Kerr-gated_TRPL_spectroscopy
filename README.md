# TRPL · Kerr Studio Desktop

Windows 桌面精简版 Kerr 门时间分辨荧光采集与分析工作台。独立窗口运行，无浏览器地址栏；当前使用模拟信号，真实硬件模式尚未启用。

## 启动

1. 下载并完整解压本仓库，放到可写的文件夹中。
2. 准备 **64 位 Python 3.12 或更新版本**、**Microsoft Edge WebView2 Runtime** 和 **.NET Framework 4.8**。
3. 双击带图标的 **`TRPL.exe`**。首次启动会显示依赖安装进度，完成后打开软件。

`TRPL.exe` 是带图标的轻量启动器，不是捆绑 Python 的独立安装包。首次安装需要联网；Python 依赖保存在软件目录的 `.venv` 中，之后复用该环境。整个软件文件夹移动后，可继续从新位置启动；如现有虚拟环境因移动失效，删除该目录下的 `.venv`，下次启动会重建。

也可双击 `start-app.vbs` 启动。仅准备环境时，在软件目录运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-app.ps1 -SetupOnly
```

可为 `TRPL.exe` 创建桌面快捷方式；快捷方式会沿用 TRPL 图标。启动器自身的只读文件检查命令为 `TRPL.exe --check`，不会打开软件或安装依赖。

## 使用

- **Acquisition**：实时预览、位移台调节、分段扫描、背景采集、暂停/恢复与 HDF5 保存。确认覆盖时直接替换同名实验文件，不生成备份。
- **Analysis**：`Decay & fit` 查看指定波长的衰减并拟合；`Spectrum` 选择多个 delay 叠加波长光谱，颜色按时间先后映射。导入文件只影响 Analysis。
- **Spectral Calibration**：模拟 CCD Full image / FVB 采集、选行提取、谱线指认、波长标定拟合及 JSON 导入/导出。
- 显示轴的最小值/最大值可点击原位编辑，Enter 应用，Esc 取消。

实验与运行日志默认位于 `data`，可另选实验保存目录。程序采用有界异步批量写入和前端增量更新；状态日常增量同步，每 60 秒完整同步。退出时仍会确认未保存或正在采集的数据。

遇到启动问题，可查看 `data/desktop-error.log` 与 `data/desktop-service.log`。默认本地端口为 8765；退出另一份旧程序后再启动，避免多个目录的服务占用同一端口。

## 文件说明

仓库只保留桌面启动链、内嵌界面、采集/分析后端、依赖清单和图标。`index.html` 与 `frontend` 是桌面窗口必需的内嵌界面资源，不能单独删除。独立网页入口、浏览器启动器、测试、开发工具、历史设计资料、实验文件和本地虚拟环境均不随此版本分发。

`launcher.cs` 是 `TRPL.exe` 的完整源码，文件开头附重新编译命令。程序运行不需要安装 C# 开发工具。
