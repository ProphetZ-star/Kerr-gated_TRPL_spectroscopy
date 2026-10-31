> 历史设计记录；实际已实现功能与边界以仓库 README 和 docs/数据流检查.md 为准。

# Spectral Calibration 前端

沿用 Acquisition / Analysis 的英文文案、左侧控制面板、右侧图表、明暗主题与语义配色。此页面只实现浏览器内交互，不调用 CCD 采集或校准写入接口，不改变当前采集的波长轴。

## 页面工作流

1. **CCD acquisition**：输入曝光，Acquire frame 刷新合成 1024 × 255 图像。Full image 为未来硬件读出模式的占位。UI PREVIEW 与 Demo frame 标识图像来源。
2. **Spectrum extraction**：选择首末行，在图像上点击可移动提取带。一维谱为选中行的均值，Find peaks 从合成谱检测局部峰并做三点插值。图像预览的曝光字段不向相机发送命令。
3. **Reference spectrum**：使用演示线表或导入 CSV。字段为 `wavelength_nm,relative_intensity`，第二列可省略；允许 2–100 条唯一的正波长。演示谱线仅为插图数据，不对应实际灯谱数据库。Air / Vacuum 仅记录参考约定，不执行转换。
4. **Line assignments**：选中表格内某行后点击谱峰，编辑对应 pixel 和参考波长；支持添加、删除与启用/排除。点击位置附近有检测峰时吸附到其估计中心。
5. **Calibration model**：线性、二次、三次多项式本地预览；每阶至少要求系数数目再加一条参考线以检查残差。校验重复像素、重复波长、非单调匹配，以及整个 CCD 上的映射是否折返。修改峰位、行范围、参考约定或模型后旧结果失效。
6. **Review**：展示 pixel→wavelength、参考减模型残差、RMS、最大绝对残差和谱线覆盖范围。拟合后可将一维谱切换至 nm 轴；在 nm 轴上叠加参考谱线。像素模式下的竖线为待核对的谱线匹配位置。
7. **Export**：仅导出前端草稿 JSON，明确包含 `applied:false`、合成 CCD 来源、参考来源、提取范围、归一化多项式系数、参考对、覆盖范围与 1024 点预览轴。该草稿不是现有后端导入 schema。Apply to acquisition 禁用，预留后端对接。

## 科学与实现边界

- 谱线匹配需要人工核对，峰检测不能判断元素身份或消除谱线混叠。
- 残差反映参考线上的一致性，不等于波长绝对准确度。覆盖范围之外的导出轴为多项式外推，未被参考线验证。
- 前端使用归一化像素 `(pixel - 511.5) / 511.5` 拟合，以减小高次幂尺度差异；正式标定需在后端重算并记录来源、单位、设备及光栅状态。
- 后续接入 CCD Image 模式时，应确认实际传感器尺寸、曝光、增益、ROI、饱和像素与坏点，并在安装标定前核对光谱仪设置。

## 验证

`node tests/calibration-browser.cjs` 提供静态文件与 mock 状态，不启动仪器后端。验证图像预览、行范围校验、选峰、CSV、重复峰与阶数校验、映射单调性、nm 轴、草稿来源标记、结果失效、明暗主题及 1440/1024/390 px 布局。检查过程中没有相机、实验或标定写入请求，页面脚本错误为零。

预览：`test-output/calibration-light.png` / `calibration-dark.png`。测试摘要：`test-output/calibration-verification.json`。
