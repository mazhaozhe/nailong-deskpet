# 发布说明 · 奶龙桌宠

## 当前源码

通知页新增 PDF、DOCX 和 TXT 文件入口，支持本地中英文 OCR。普通模式按模型设置拆解，Agent 模式复核文档并在模型不可用时标明回退。相识天数按北京时间计算，聊天和待办参与亲密度计分。

在仓库根目录准备好依赖后运行：

```powershell
npm run dist --prefix apps/deskpet/desktop
```

产物是 `apps/deskpet/desktop/dist/奶龙桌宠 Setup 1.0.0.exe`。构建会检查包内 PDFium、ONNX Runtime 和本地 OCR 模型。源码启动、测试与验收命令见 [README](../README.md)。

## 已发布的 Windows 安装包

从 [v1.0.0 Release](https://github.com/mazhaozhe/nailong-deskpet/releases/tag/v1.0.0) 下载 `Setup.1.0.0.exe`，双击安装。该附件对应此前构建，源代码中的本地 OCR 版需重新打包。

- 已发布附件大小为 148,532,255 字节。可用 `Get-FileHash .\Setup.1.0.0.exe -Algorithm SHA256` 核对以下值。
- SHA256 为 `70f7281451b884a5b559cb555fed24bc25538108506ee792865e5c36f7896c2b`。
- 安装包未签名。下载或运行时可能出现 Windows 信誉提示，请先核对下载来源和哈希。
- 业务数据写在 `%APPDATA%\nailong-deskpet-client\deskpet\`，卸载默认保留。

## 奶龙图像素材与授权

- 素材与动作表来源：[zhangkk833-byte/dsh-nailong-pet](https://github.com/zhangkk833-byte/dsh-nailong-pet)。
- 上游图像来源：[Maple498/nai-wa-codex-pet](https://github.com/Maple498/nai-wa-codex-pet)（Codex Pet v2）。
- 图像许可证：[Creative Commons Attribution 4.0 International（CC BY 4.0）](https://creativecommons.org/licenses/by/4.0/)。
- 随包授权文件：`apps/deskpet/desktop/assets/nailong/ASSET-LICENSE.md`。

本项目使用 74 张 RGBA PNG、13 组 `manifest.json` 动作表及上述授权文件。`meta.totalFrames=83` 是动作帧引用次数，74 是唯一 PNG 文件数。上游已完成切分、裁切、地面线对齐与动作编排；本项目未改帧、未补帧、未调色、未修改任何像素，只新增本地序列帧播放和交互代码。「奶龙」原型的形象权、商标权或其他第三方权利不在上游授权保证范围内，本项目不宣称该形象为原创。相同署名同时放在根目录 `README.md` 与应用内「关于」。

## 第三方代码授权

`apps/deskpet/desktop/vendor/petex/`：Windows 原生拖拽参考实现，取自 [d4x1/petex](https://github.com/d4x1/petex)（commit `43981e2`），许可证 **Apache License 2.0**。随包保留 `LICENSE` 与 `SOURCE.md`；上游源文件原样保留，本项目只改造了页面侧的指针生命周期与动画接入。

本项目自身代码为 MIT（见根目录 `LICENSE`）；第三方素材与代码各自按其许可证授权。
