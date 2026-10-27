# 桌面窗口与实现语言评估

评估日期：2026-10-26。本文按当前 `AutoLrcAligner.pyw`、引擎代码和依赖清单做架构评估；没有设备性能基准数据，因此不对不同语言的实际帧率、启动时间或打轴耗时给出虚构数值。

## 当前结构与实际瓶颈

- 桌面窗口使用 Python `tkinter` / `ttk`，主要界面、播放器、标签编辑器和设置都集中在约 8,200 行的 `AutoLrcAligner.pyw`。圆角卡片、按钮、页签、进度条等由 Canvas 自绘。
- 标签读取与保存、在线歌词搜索、播放加载、重新打轴和训练等耗时操作已经放入后台线程，并通过 Tk 的事件队列更新界面。当前实现具备基本的界面与工作任务隔离；仅凭代码无法证明窗口正在因 Tkinter 本身而卡顿。
- 模型前向计算调用 PyTorch。卷积、循环网络、张量变换等主要计算由 PyTorch 的原生 CPU / CUDA 内核完成。PyTorch 官方文档特别说明，Python 前端不必然比 C++ 慢，数值密集工作通常已进入原生代码。[PyTorch C++ 前端说明](https://docs.pytorch.org/cppdocs/frontend.html)
- 从源码可直接看到更明确的纯 Python 热点：`upstream/t2l/mtl/utils.py` 的 `alignment_bdr` 对音频帧和音素路径执行嵌套动态规划循环。若长音频的打轴时间确实偏长，这段更适合先单独计时并做 JIT / 原生实现，而不是先重写整个窗口。
- 当前发布包的大小主要由 Python / PyTorch / 音频运行库和模型决定；Tkinter 随 Python 提供。换一种 UI 语言不会移除本地模型运行所需的依赖，除非同时移植或替换推理链。

## 候选方案

| 方案 | 窗口体验 | 对打轴性能的预期 | 发布与维护代价 | 判断 |
|---|---|---|---|---|
| 保留 Python + Tkinter | 能继续沿用当前深色 / 浅色定制、音频编辑和控件行为；自绘控件与单文件结构较难长期扩展 | 对 PyTorch 推理基本无直接影响；已能用后台线程保持交互 | 不新增框架、语言或 IPC；Python 安装环境仍是必需项 | **当前版本建议保留**。先模块化界面，再按实际测量优化热区 |
| Python + PySide6 / Qt | 更完整的桌面控件、布局、可访问性和高 DPI 支持；继续使用 Python 业务层 | 对模型计算没有直接加速 | 要重写窗口层并增加 Qt 运行库；发布需处理 Qt 第三方许可。Qt for Python 社区版按 LGPLv3 / GPLv3 条款提供，须检查具体分发方式。[Qt for Python 许可](https://doc.qt.io/qtforpython-6/commercial/index.html)、[部署选项](https://doc.qt.io/qtforpython-6/deployment/index.html) | 若主要目标是换更丰富的 Python 桌面 UI，可考虑；不适合作为性能优化方案 |
| C# + WPF | Windows 原生桌面体验、XAML 数据绑定、成熟控件、高 DPI 和硬件加速图形；只支持 Windows。[Microsoft WPF 文档](https://learn.microsoft.com/en-us/dotnet/desktop/wpf/overview/) | 若推理仍留在 Python / PyTorch 后端，打轴时间大体仍由原引擎决定 | 需要重写整套 UI；Python 后端要作为独立进程运行，增加消息协议、生命周期、错误传递和两个运行环境的维护 | **若未来明确优先打造 Windows 原生产品，这是最合适的界面迁移方向**；当前没有已测量的窗口瓶颈，暂不建议整体迁移 |
| Rust + Tauri + Web UI | Web 技术适合高自由度视觉界面；Windows 用 WebView2，能构建 MSI 或 Setup 安装器。[Tauri WebView2](https://v2.tauri.app/reference/webview-versions/)、[Windows 安装器](https://v2.tauri.app/distribute/windows-installer/) | Python 推理侧车仍存在时，模型性能没有直接收益 | 要引入 Rust、前端构建链和跨进程桥接；Windows 发行还要考虑 WebView2 安装 / 离线体积。[Tauri 前置条件](https://v2.tauri.app/start/prerequisites/) | 如果目标改为跨平台或 Web 团队式开发可考虑；对当前 Windows 本地工作站不如 WPF 直接 |
| 全部改为 C++ / Rust 原生 | 控制能力最高 | 只有重写并优化实际热区才可能更快；重写界面本身不提高模型吞吐 | 需要替换音频、歌词读音转换、PyTorch 调用、训练和 Windows 标签处理等多个成熟库的集成，验证与维护成本最高 | **不推荐全量改写**。PyTorch 官方也将 C++ 前端定位为有多线程、延迟或部署需求时的选择，而不是 Python 的无条件替代品 |

## 建议路线

1. **本次发布继续使用 Python + Tkinter。** 这保留目前已完成的桌面功能和训练链，不再叠加另一套运行时，也不会把本地音频处理引入浏览器或云端。
2. **先把 8,200 行窗口文件拆成界面组件、状态 / 设置、音频编辑和打轴流程模块。** 这是可维护性改进，不承诺带来打轴速度提升。
3. **若后续数据显示打轴耗时主要落在 `alignment_bdr`，优先对这个动态规划内核做 Numba JIT 或独立原生扩展试验。** 当前依赖清单已有 Numba；试验需用一组锁定音频 / 歌词输出对照旧实现，检查时间戳一致性，并分别记录 CPU / GPU、短 / 长歌曲的冷启动和稳态耗时。
4. **若目标是更成熟的 Windows 窗口、控件无障碍和安装体验，再评估 C# WPF 前端 + Python 模型工作进程。** 保持 Python 引擎独立，可避免在一次迁移中连模型、音频和训练实现一起重写。
5. **只有目标转为跨平台桌面产品时，再评估 Tauri。** 若主要诉求只是视觉更现代，PySide6 能复用 Python 逻辑，但需先审查 Qt 的 LGPL / GPL 许可与体积。

下一步性能判断应把窗口绘制、启动时间、标签读写、声学推理、动态规划分开测量。当前代码检查不能替代真实设备基准，因此本次没有将界面迁移或内核替换作为已实施变更。