# 图像资产授权与署名 · Asset License & Attribution

## 上游来源

本项目的全部图像资产派生自：

- **项目**：`Maple498/nai-wa-codex-pet`
- **文件**：`spritesheet.webp`（1536×2288，8 列 × 11 行，Codex Pet v2 图集格式）
- **授权**：**Creative Commons Attribution 4.0 International（CC BY 4.0）**
- **sha256**：`f1960d06f031f11dbe2037548fa0b18a41b5f0666e7812a4bf73c515be6b1c17`

原始副本保存在 `assets/raw/spritesheet.webp`，未作任何像素修改。

## 本项目的改动

上游图集「原样使用」，只做了**切分与打包**：

- 按 8×11 网格切出 74 张单帧 PNG（`assets/frames/`）；
- 每行在 **x 方向统一裁切**（保证走路循环不会左右抖动）；
- 每帧在 **y 方向单独裁切**，并让内容底线对齐到虚拟单格的地面线（y=203），
  使蜷缩/躺卧类姿势与站立姿势踩在同一条地板上；
- 编写 `assets/manifest.json` 描述帧序、帧率、循环方式、朝向与移动速度。

**没有重绘、没有补帧、没有调色、没有修改任何像素内容。**

## 建议署名

```text
桌面宠物素材来源于 Maple498/nai-wa-codex-pet，采用 CC BY 4.0 授权；
本项目对其进行了网格切分与动画编排（未修改像素内容）。
```

协议原文：<https://creativecommons.org/licenses/by/4.0/>
中文摘要：<https://creativecommons.org/licenses/by/4.0/deed.zh-hans>

## 原型形象声明

"奶龙 / 奶蛙"的网络流行形象原型，不属于上游项目或本项目所能确认或授予的权利范围。
CC BY 4.0 仅覆盖上游维护者实际创作的像素绘制与图集制作，
不保证覆盖形象权、商标权、人格权或其他第三方权利。
使用者应根据自己的发布地区与用途自行判断。

## 本项目代码

`main.js`、`preload.js`、`src/`、`tools/` 下的代码：MIT。
