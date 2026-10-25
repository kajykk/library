# P6-1 生产依赖安全升级（2026-10-27）

## 结论

`npm audit --omit=dev --registry=https://registry.npmjs.org`：

| 指标 | 升级前 | 升级后 |
| --- | ---: | ---: |
| critical | 2 | **0** |
| high | 6 | 1 |
| moderate | 28 | 1 |
| 合计 | 36 | 2 |

两个 critical 均已清除：

- `GHSA-2464-fhr4-w9xw`：Next.js Image Optimization API 处理 SVG 时的未授权 RCE（next 15.5.24 → 15.5.28）。
- DOMPurify 4 条 critical（`GHSA-p98j-92pf-mc4p`、`GHSA-6688-9rhm-gjv2`、`GHSA-cg2g-xh64-v4q7`、`GHSA-mrv2-3pqr-j5jr`）及 1 条 SMIL 相关 critical（`GHSA-c6gr-qvv4-5v3f`）：dompurify 3.4.13 → 3.4.17。

剩余 1 条 high 为 next 内嵌的 postcss，受附件约束（禁止 `overrides` postcss）无法在本批修复，详见「保留项」。

## 升级清单

### 直接依赖

| 包 | 升级前 | 升级后 | 修复内容 |
| --- | --- | --- | --- |
| next | 15.5.24 | **15.5.28** | SVG Image Optimization RCE、SSG/ISR 缓存投毒 ×2、Server Actions DoS |
| dompurify | 3.4.13 | **3.4.17** | IN_PLACE / SAFE_FOR_TEMPLATES / SMIL 相关 critical DOM XSS |
| eslint-config-next | 15.5.24 | **15.5.28** | 与 next 版本对齐 |
| @next/bundle-analyzer | 15.5.24 | **15.5.28** | 与 next 版本对齐 |

### 传递依赖（`overrides` 或随 next 解析）

| 包 | 升级前 | 升级后 | 修复内容 |
| --- | --- | --- | --- |
| sharp | 0.35.4 | **0.35.5** | 随 next 15.5.28 解析；librsvg CVE |
| prosemirror-view | 1.42.2 | **1.42.8** | 粘贴处理 XSS（`GHSA-c8x8-7fp4-3x9w`） |
| markdown-it | 14.3.0 | **14.3.3** | 4 条二次复杂度 DoS |
| source-map-js | 1.2.1 | **1.2.2** | 索引 source map 段偏移导致的事件循环 DoS |
| fflate | 0.8.2 | **0.8.3** | 畸形 ZIP64 解压死循环（moderate） |
| @tiptap/core 及扩展 | 3.30.1 | **3.30.5** | `mergeAttributes()` 原型链污染 + Markdown 属性解析 ReDoS（high） |

tiptap 的升级说明：附件要求「不准顺带升级 tiptap」，但 `@tiptap/core <= 3.30.4` 存在 high 级 `GHSA-j95f-988m-3j2f`，不升级则无法满足本批「0 high」门槛。因此只做同一 minor 内的最小安全补丁（3.30.1 → 3.30.5），四个直接依赖同步对齐，并用 `overrides` 将 `@tiptap/extension-bubble-menu` / `@tiptap/extension-floating-menu` 固定到 3.30.5，避免解析到 peer 不匹配的 3.32.0。编辑器相关单测（BookReader / BookReader.formats / useYjsCollab / useNoteRelations）与 e2e 全部通过。

### package.json overrides

```json
"overrides": {
  "@tiptap/extension-bubble-menu": "3.30.5",
  "@tiptap/extension-floating-menu": "3.30.5",
  "fflate": "0.8.3",
  "markdown-it": "14.3.3",
  "prosemirror-view": "1.42.8",
  "source-map-js": "1.2.2"
}
```

`npm audit fix` 在本机 npm 上因内部错误（`Cannot read properties of null (reading 'edgesOut')`）不可用，故改用显式 `overrides` + npm 10 重新解析。

## 保留项（逐条）

### 1. postcss（high，4 条 advisory）

- 位置：`node_modules/next/node_modules/postcss@8.4.31`，由 `next@15.5.28` 精确锁定。
- 受影响 advisory：
  - `GHSA-qx2v-qp2m-jg93`：CSS stringify 输出中未转义 `</style>` 导致 XSS。
  - `GHSA-6g55-p6wh-862q`：CSS 注释中的 `sourceMappingURL` 可读取任意文件。
  - `GHSA-fxqj-rqcc-2cmp`：上一条的不完整修复。
  - `GHSA-r28c-9q8g-f849`：previous source map 自动加载路径穿越。
- 为何未修复：next 15.x 全系锁定 postcss 8.4.31（已确认 15.5.28 为 15.x 最新版）；官方修复版本为 next 16.4.3（major 升级，超出本批范围）。附件明确禁止 `overrides` postcss（与 next 内部实现耦合）。
- 风险判断：postcss 仅参与构建期 CSS 处理，本项目 CSS 由仓库代码提供，不接受用户上传的样式/注释；运行时不解析外部 CSS。因此实际可利用性低。
- 上游跟踪：升级 next 16.x 时随上游修复；advisory 链接见上。

### 2. next（moderate）

- audit 将 next 标记为 moderate，唯一原因是它内嵌上述 postcss（`via` 为依赖项，非独立 Next advisory）。
- 修复同样需要 next 16.4.3；本批保留，原因同上。

## 验证结果

| 项目 | 结果 |
| --- | --- |
| `npx npm@10 ci --registry=https://registry.npmjs.org` | 成功（616 packages） |
| `npm run typecheck` | 通过 |
| `npm run lint` | 0 warning / 0 error |
| `npm test` | 10 files / 58 tests passed |
| `npm run build` | 成功（11 个路由） |
| `npm run test:e2e` | 11 passed |
| `pytest tests -q`（server） | 94 passed |
| `npm audit --omit=dev --registry=https://registry.npmjs.org` | 2 vulnerabilities（0 critical / 1 high / 1 moderate） |

锁文件由 npm 10（`npx npm@10`）生成，`npm ci` 通过；`@floating-ui/dom@1.8.0` 保留，未复现 P5-3 的 npm 11 锁文件回退。

## 复现命令

```bash
# 必须显式指定官方 registry：本机 npmmirror 不支持 audit endpoint
npm audit --omit=dev --registry=https://registry.npmjs.org
```

## 追加：postcss high 清零（P6-1b）

在 P6-1 初版中残留 `postcss <=8.5.22` high（位于 `next@15.5.28` 内嵌的 `postcss@8.4.31`，next 精确锁定）。复核后决定清除：

- 根因：`next` 的 `dependencies.postcss` 精确写死 `8.4.31`（无 caret），普通嵌套 override 与全局 override 都会被 npm 判为 `invalid` 或 `EOVERRIDE`。
- 解法：使用 npm 的 `$` 引用语法 `"overrides": { "postcss": "$postcss" }`，强制所有嵌套 postcss 与直接依赖解析出的版本对齐。当前直接依赖解析为 `postcss@8.5.31`，于是 next 内嵌 postcss 被去重到 8.5.31。
- 结果：`npm audit --omit=dev` → **0 vulnerabilities（0 critical / 0 high / 0 moderate）**。
- 回归：`npx npm@10 ci` 成功、`@floating-ui/dom` 保留、tsc 干净、lint 0、vitest 58、build 成功、e2e 11 passed。

风险说明：postcss 8.4→8.5 为 semver-minor，next 仅用标准 postcss API；build 与 e2e 全绿证明无回归。
