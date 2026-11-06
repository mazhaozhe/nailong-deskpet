# Release 说明 · 阶段 5

## 奶龙图像素材与授权

- 素材与动作表来源：[zhangkk833-byte/dsh-nailong-pet](https://github.com/zhangkk833-byte/dsh-nailong-pet)。
- 上游图像来源：[Maple498/nai-wa-codex-pet](https://github.com/Maple498/nai-wa-codex-pet)（Codex Pet v2）。
- 图像许可证：[Creative Commons Attribution 4.0 International（CC BY 4.0）](https://creativecommons.org/licenses/by/4.0/)。
- 随包授权文件：`client/assets/nailong/ASSET-LICENSE.md`。

本阶段使用用户侧预先落地的 74 张 RGBA PNG、13 组 `manifest.json` 动作表及上述授权文件。`meta.totalFrames=83` 是动作帧引用次数；74 是唯一 PNG 文件数。上游已完成切分、裁切、地面线对齐与动作编排，本项目未改帧、未补帧、未调色、未修改任何像素，只新增本地序列帧播放和交互代码。

“奶龙”原型的形象权、商标权或其他第三方权利不在上游授权保证范围内；本项目不宣称该形象为原创。相同署名同时放在根目录 `README.md` 与应用内「关于」。

## 本阶段边界

本文件只是阶段 5 的 Release 说明，不代表已执行打包或发布。Windows 安装包、内置 Python 运行时、签名与杀软适配留待阶段 7。
