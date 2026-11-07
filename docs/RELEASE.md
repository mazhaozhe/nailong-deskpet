# 发布说明 · 奶龙桌宠

> 本文件随仓库发布，说明本软件包含什么、第三方素材与代码如何授权，以及当前发布状态。
> 能力说明见根目录 `README.md`，开发交接见 `docs/HANDOFF.md`。

## 本软件包含什么

一个常驻 Windows 桌面的透明置顶小窗：贴一张通知 → 拆成清单 → 落成待办 → 下次回来主动补报「还差什么」→ 导出 docx。附托盘与系统通知、跨会话记忆、「认识你的第 N 天」成长展示。

- **默认规则版**：断网、没有 Key 也能完整走完主流程；模型可用时改用固定结构的 JSON 拆解，界面诚实标注「模型拆解 / 规则兜底」。
- **API Key 由使用者自填**：主进程 `safeStorage` 加密保存在本机，只注入 Python 子进程环境变量，不进仓库、包、日志或渲染进程。三态开关（关 / 自动 / 开）默认自动。
- **自由闲聊与 Agent 模式为可选能力，默认关闭**：Agent 模式启用完整工具链与联网能力，危险工具需弹窗审批、60 秒未响应默认拒绝，检索结果有域名白名单与注入清洗。
- **不含任何预置数据、演示故事或假历史**：每位使用者从干净的第 1 天独立培养，现场展示的是本机真实状态。

## 奶龙图像素材与授权

- 素材与动作表来源：[zhangkk833-byte/dsh-nailong-pet](https://github.com/zhangkk833-byte/dsh-nailong-pet)。
- 上游图像来源：[Maple498/nai-wa-codex-pet](https://github.com/Maple498/nai-wa-codex-pet)（Codex Pet v2）。
- 图像许可证：[Creative Commons Attribution 4.0 International（CC BY 4.0）](https://creativecommons.org/licenses/by/4.0/)。
- 随包授权文件：`apps/deskpet/desktop/assets/nailong/ASSET-LICENSE.md`。

本项目使用 74 张 RGBA PNG、13 组 `manifest.json` 动作表及上述授权文件。`meta.totalFrames=83` 是动作帧引用次数，74 是唯一 PNG 文件数。上游已完成切分、裁切、地面线对齐与动作编排；本项目未改帧、未补帧、未调色、未修改任何像素，只新增本地序列帧播放和交互代码。「奶龙」原型的形象权、商标权或其他第三方权利不在上游授权保证范围内，本项目不宣称该形象为原创。相同署名同时放在根目录 `README.md` 与应用内「关于」。

## 第三方代码授权

`apps/deskpet/desktop/vendor/petex/`：Windows 原生拖拽参考实现，取自 [d4x1/petex](https://github.com/d4x1/petex)（commit `43981e2`），许可证 **Apache License 2.0**。随包保留 `LICENSE` 与 `SOURCE.md`；上游源文件原样保留，本项目只改造了页面侧的指针生命周期与动画接入。

本项目自身代码为 MIT（见根目录 `LICENSE`）；第三方素材与代码各自按其许可证授权。

## 当前发布状态

**尚未执行打包或发布**：仓库当前提供可直接在本机运行的源码，启动方式见 `README.md` 第二节。Windows 安装包、内置 Python 运行时、签名与杀软适配留待阶段 7；届时须把后端与 Python 运行时纳入打包、让业务库落可写的用户数据目录、显式排除 `.env`，并在无 Python 的干净机器上另做一套验收。exe 发布后从本仓库的 Releases 页面下载。
