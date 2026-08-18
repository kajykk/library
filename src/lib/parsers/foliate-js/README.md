# foliate-js（vendored 副本）

本项目将上游 foliate-js 直接复制进 `src/lib/parsers/foliate-js/`，
避免每次安装重新拉取依赖（原项目体积较大且无独立 npm 发布）。

- 上游仓库: https://github.com/johnfactotum/foliate-js
- 上游版本: 2024-12 快照（EPUB 3 解析 / EPUB CFI / MOBI）
- 复制时间: 2026-08-18（项目改造期间）
- 包含文件: epub.js / epubcfi.js / mobi.js / LICENSE（MIT）

更新方式（如需升级）：
1. `git clone https://github.com/johnfactotum/foliate-js` 到临时目录
2. 对比并覆盖上述文件（注意 `epub.js` 依赖 `epubcfi.js` 的 API 兼容性）
3. 运行 `npm run lint` 与本项目测试验证（副本目录已加入 .eslintignore）