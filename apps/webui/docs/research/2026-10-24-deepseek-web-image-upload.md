# DeepSeek Chat Web 图片上传 / 查看 逆向报告

- 目标：`https://chat.deepseek.com/`
- 分析日期：2026-10-24
- 目的：为「复刻图片上传后的气泡样式 + 图片查看（大图/缩放/切换）」提供可直接落地的实现依据
- 结论标注：**【实证】**= 有产物代码/CSS 直接证据；**【推断】**= 由证据推导，未在产物中直接看到

---

## 0. 结论速览

| 关注点 | 结论 | 证据强度 |
|---|---|---|
| 上传接口 | `POST /api/v0/file/upload_file`，`multipart/form-data`，字段名 `file` | 实证 |
| 上传附加头 | `x-thinking-enabled`、`x-model-type`、`x-file-size` | 实证 |
| 进度 | 走 HTTP 上传进度事件，归一化到 `0~1` 存 `fileUploadInfo.percent` | 实证 |
| 解析状态 | 上传成功 ≠ 可用，需轮询 `GET /api/v0/file/fetch_files?file_ids=...` 直到 `SUCCESS` | 实证 |
| 图片地址 | `https://files.deepseeksvc.com/api{signature}` + `?ty=t\|p` + `&fmt=jpeg\|webp` | 实证 |
| 缩略图档位 | `ty=t`（列表/多图，128~小图）、`ty=p`（单图/大图预览） | 实证 |
| 消息内单图尺寸 | 以 **240px** 为基准的等比盒，宽高均 ≤ 240 | 实证 |
| 消息内多图尺寸 | 固定 **64×64**，`object-fit: cover` | 实证 |
| 图片圆角 | **16px**，再用 `::after` 内描边 1px | 实证 |
| 大图查看器 | 自研 `FancyBox`（不是 photoswipe/lightbox 类库），`createPortal` 到 `body`，`z-index: 2000` | 实证 |
| 查看器交互 | 点击缩放、滚轮缩放、拖拽、左右箭头键、缩略图条、ESC 关闭、点遮罩关闭 | 实证 |
| 缩放范围 | `[1, max(4, 2 / naturalToContentScale)]` | 实证 |
| 下载原图 | 查看器默认工具栏只有「关闭」，**未发现下载按钮** | 实证（未见） |

一句话概括它的产品思路：

> **本地立即出图（objectURL 秒显）→ 后台静默上传+解析 → 服务端签名 URL 分档下发 → 消息里用「240 基准等比盒 / 64 方格」渲染 → 点开进自研全屏查看器做缩放与切换。**

---

## 1. 取证环境与产物

### 1.1 页面注入信息

`<meta name="commit-id" content="26090614">`，`appVersion: "2.7.0"`（在 bundle 里以 `appVersion:"2.7.0"` 出现）。

### 1.2 静态产物（页面直接引用，CDN 无鉴权）

| 文件 | 大小 | SHA-256 |
|---|---|---|
| `main.d1d163b7e4.js` | 1,609,382 | `3cf5185515eae96d2170c8fa9c5ab80270237b15d8db7f5c8c18da356b736b88` |
| `main.cc59a63cb5.css` | 298,333 | `9cb0b00b626da8664b88ac81ed6e54e7ebe31e6ffc02ae94e15a88180125da5a` |
| `default-vendors.a2a242fbd2.js` | 1,033,287 | `ec1ad1ba52fb7b991c8efec29b65774908a11497b90b3ec0d4d382cc3f04b76f` |

> 关键点：**没有 webpack 分包加载器**（`webpackChunk` / `__webpack_require__.u` 均未命中），业务代码全在这一个 1.6MB 的 `main.js` 里。直接全文检索即可，不用追 chunk。

### 1.3 响应头 / 服务端

根路径返回 200 + SPA 骨架，`/sign_in`、`/manifest.json` 都回同一份 HTML（前端路由兜底）。静态资源走 `fe-static.deepseek.com`，用户文件走 `files.deepseeksvc.com`。

---

## 2. 技术栈判断（指纹）

| 项 | 结论 | 证据 |
|---|---|---|
| UI 框架 | React（`createRoot` + `react-dom`） | `default-vendors.js` 中 `rendererPackageName:"react-dom"` |
| React 版本 | **18.3.1 与 19.2.7 两套并存**（渐进升级中） | `version:"18.3.1"` / `e.version="19.2.7"` 同时存在 |
| 状态管理 | zustand（`Y.vt()` 造 store，`getState()/setState()` + `subscribe`） | `zustand` 命中 11 次 |
| 不可变 | immer（`$.D` 包装、`(0,$.D)((set,get)=>({...}))`） | `immer` 命中 |
| 构建 | Rspack（`rspack` 字符串命中，webpack runtime 兼容层） | `rspack` |
| 样式 | **CSS Modules**（构建后是 hash 类名，如 `cc804671` / `_2571442`），外加一套 `ds-*` BEM 设计系统 | CSS 中 `ds-button--*`、`ds-fancy-box__*` |
| Markdown | marked/highlight/katex/mermaid 均在包内 | `katex` 18、`mermaid` 51 |
| 图表 | `@antv/g6` | `g6` |
| 传输 | 自研 `ky` 风格 `http.http.post/get` + `EventSource`（SSE 流式） | `eventsource` |

### 2.1 CSS Module 映射是怎么还原的

CSS Modules 在构建时把 `styles.image` 编译成了 `e.exports={kL:"cc804671",...}` 的形式，映射表**内联在 main.js 1,030,000 偏移附近**（形如 `23801(e){"use strict";e.exports={...}}`）。所以：

1. 从组件代码里拿到 `var gh=n(23801)`；
2. 搜 `23801(e)` 得到 `{kL:"cc804671", vk:"_4037039", Ew:"_2571442", ...}`；
3. 再去 `main.css` 里查 `.cc804671{...}` 拿真实样式。

本报告所有「精确像素值」都是这样一条链拿到的，不是目测。

关键模块 id（复刻时对照用）：

- `23801` → 单张图片盒子的类名表
- `13732` → 图片状态遮罩 / 状态文案
- `53138` → 内容审核遮罩
- `17138` → 附件列表容器 + 文件卡片
- `50928` → 上传进度环

---

## 3. 上传链路（完整时序）

```
选择文件 / 拖拽 / 粘贴
   │
   ├─ 1. addLocalFileForUpload()  ← 立刻生成 localId，进 fileInfoMap
   │      └─ prepareLocalFilePreview()：objectURL 当本地预览图（秒显）
   │
   ├─ 2. getFileSizeProblem()     ← 超过 maxSizeMB 直接标记 validation_failed，不发起请求
   │
   ├─ 3. （可选）.heic/.heif → canvas 转 WebP（最长边 4096）
   │
   ├─ 4. doUploadFile()
   │      POST /api/v0/file/upload_file   (FormData, onUploadProgress)
   │      headers: x-thinking-enabled / x-model-type / x-file-size
   │      → 返回 biz_data = 文件对象（含 signed_path / status / is_image / width / height）
   │
   ├─ 5. localId → serverId 迁移（imageInfoMap / resourceStore 都 move）
   │
   └─ 6. status != SUCCESS → 轮询 GET /api/v0/file/fetch_files?file_ids=a,b,c
          PENDING → PARSING → SUCCESS / FAILED / CONTENT_FILTER / CONTENT_TOO_LONG / CANCELLED / CONTENT_EMPTY
```

### 3.1 入口与校验

- 三个入口共用同一套上传器：**按钮选择 / 拖拽（`f2` drop 区）/ 粘贴（textarea `paste` 事件）**。
  粘贴文本超过阈值（默认 2000 字，服务端 `file_feature.paste_text_to_file_character_limit` 可覆盖）时，会把文本**变成一个 `.txt` 附件**上传，并提供「粘贴原文至输入框」的回滚按钮。
- 数量与大小限制来自**服务端下发的模型配置**，前端有兜底默认：

```js
// 兜底默认值
const DEFAULT_LIMIT = { maxCount: 50, maxSizeMB: 100 };   // 0x6400000 = 100MB

// 服务端覆盖（model_configs[].file_feature）
{
  max_input_file_count,      // 单条消息附件上限
  max_upload_file_size,      // 单文件字节上限
  support_file_exts,         // 允许扩展名白名单
  enable_thumbnail,          // 是否允许图片缩略图渲染（false 时图片当普通文件）
  conflict_with_search,      // 有附件时是否禁用联网搜索
  token_limit / token_limit_with_thinking,
  paste_text_to_file_character_limit
}
```

**重要开关**：`file_feature.enable_thumbnail !== true` 时，图片**不按图片渲染**，而是走文件卡片。这是「灰度/降级」的开关点。

### 3.2 本地秒显（复刻的关键体验点）

```js
// d3(file, localId)
const url = URL.createObjectURL(file);
imageInfoMap[localId] = { localThumbnailUrl: url, width: null, height: null };
const size = await measureImage(url);              // new Image() → naturalWidth/Height
if (size) imageInfoMap[localId].width = size.width, .height = size.height;
```

- `localThumbnailUrl` **不是压缩后的缩略图**，而是原文件的 blob URL（宽度已知后可直接算比例，所以不需要压缩）。
- 真正需要「小图」的场景（喂给模型的图）才走 `128×128` 居中裁切 + WebP 编码：

```js
const THUMB = { targetWidth: 128, targetHeight: 128, sizeThreshold: 192 };
// jpeg/bmp → image/jpeg，其余 → 支持 WebP 就 WebP，否则 PNG
```

- 上传成功后 **不立刻换掉 blob**：先用 `img.decode()` 预热服务端 URL（并发 4，带 Map 缓存），decode 成功才切换，避免「白闪」。

```js
const preloadImage = (url) => new Promise(resolve => {
  const img = new Image();
  img.src = url;
  img.decode().then(() => resolve(true), () => resolve(false));
});
// 并发限制 4；成功后把资源从「本地 blob」降级释放
```

发送消息后还有一次**预取**：附件里只有 1 张图 → 用 `ty=p` 预取大图，否则用 `ty=t` 预取缩略图。

### 3.3 HEIC/HEIF 转 WebP（重要，国内 iPhone 用户必需）

```js
const MAX_EDGE = 4096;

// 触发条件：扩展名 ∈ {jpg,jpeg,png,heic,heif} 或 MIME ∈ {image/jpeg,image/png,image/heic,image/heif}
// 且浏览器能 WebP 编码（canvas.toBlob('image/webp') 探测）
const scale = Math.min(1, MAX_EDGE / Math.max(w, h));
canvas.width = Math.round(w * scale);
canvas.height = Math.round(h * scale);
canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
canvas.toBlob(blob => new File([blob], rename(file.name, 'webp'), {
  type: 'image/webp', lastModified: file.lastModified
}), 'image/webp');
```

- **无损替换**：转换失败直接回退原文件（`try/catch` 包住，`return file`），不会阻断上传。
- 只对**需要转换**的格式做，jpeg/png 在不支持 WebP 编码的环境里也会走这条路（把 png 变 webp 省带宽）。

### 3.4 上传请求（可 1:1 复刻）

```js
POST /api/v0/file/upload_file
Content-Type: multipart/form-data (浏览器自动带 boundary)
x-thinking-enabled: "1" | "0"
x-model-type: "<当前模型 model_type>"
x-file-size: "<file.size>"        // 字符串

body: FormData  ← form.append('file', file)   // 字段名固定 "file"，没有其它字段
```

```js
const form = new FormData();
form.append('file', file);
const res = await http.post('/api/v0/file/upload_file', {
  body: form,
  headers: { 'x-thinking-enabled': thinking ? '1' : '0', 'x-model-type': modelType, 'x-file-size': String(file.size) },
  onUploadProgress: (e) => setPercent(clamp(e.percent / 100, 0, 1)),
  signal: abortController.signal,
});
// 统一响应信封：{ data: { biz_code, biz_data, biz_msg } }
```

- `biz_code !== 0` → 失败，`biz_msg` 就是用户可读错误。
- 有 `AbortController`，删除附件 = abort 请求。
- 重试是**同一个方法 + 一个 `isRetry` 标志**，成功后走同一套 `move(localId → serverId)`。

### 3.5 服务端返回的文件模型

从 `no(g)` 归一化函数与 JSON schema 可完整还原（snake_case 是线上字段）：

```ts
interface ServerFile {
  id: string;
  signed_path?: string | null;      // 签名路径，拼图片 URL 用
  file_name: string;
  file_size: number;
  status: 'PENDING' | 'PARSING' | 'SUCCESS' | 'FAILED'
        | 'CONTENT_FILTER' | 'CONTENT_TOO_LONG' | 'CANCELLED' | 'CONTENT_EMPTY';
  retryable?: boolean | null;
  audit_result?: 'pass' | 'reject' | 'unknown' | null;
  token_usage?: number | null;
  inserted_at: number; updated_at: number;
  error_code?: number | null;
  from_share?: boolean | null;

  // is_image === true 时才有
  is_image: boolean;
  width?: number;
  height?: number;
}
```

前端模型是它的 camelCase 版本（`signedPath / fileName / fileSize / isImage / auditResult / ...`），`isImage` 是**判别式字段**（true 分支才有 width/height）。

### 3.6 解析状态轮询

- 只轮询「有 serverId 且状态未结束」的文件：`getAllUnparsedFileServerIds()`，一次批量带上全部 id（逗号分隔）。
- `GET /api/v0/file/fetch_files?file_ids=id1,id2`，响应同样是 `{biz_code, biz_data, biz_msg}`。
- 有 `pollLimiter` 可以整体暂停（页面切走/条数超限）。
- 状态含义表（前端 `fileParseStatusMeaning[status].isFinished`）：`SUCCESS / FAILED / CONTENT_FILTER / CONTENT_TOO_LONG / CANCELLED / CONTENT_EMPTY` 视为结束。

### 3.7 切模型 = 重新解析（fork）

`POST /api/v0/file/fork_file_task { file_id, to_model_type }`

用户在输入框切换模型时，已上传的图片/文档要针对新模型重新解析一遍（不同模型的视觉/文本能力不同）。前端做法：

1. 先把该文件状态强制改成 `PARSING`（避免 UI 闪回旧结果）；
2. 调 fork 接口；
3. 失败给一次 `ForkFileTaskErrorCode` 的语义化提示。

> 复刻提示：如果你只有一种模型，这一步可以直接删掉；如果要学它的多模型切换，这是**必须**的一环——否则用户切模型后图片的 `token_usage` 与审核结果都是错的。

---

## 4. 图片 URL 体系（复刻里最容易被忽略的一块）

这是 DeepSeek 图片渲染的「一图多档」核心，全部集中在 4 个小函数里：

```js
const FILES_BASE = 'files.deepseeksvc.com';   // 生产
const API_PREFIX = '/api';

// 按站点决定域名（仅 dev/test 有意义，复刻可忽略）
// localhost / chat-dev → files-dev.deepseeksvc.com
// chat-test.*          → files-test.deepseeksvc.com
// 其它                 → files.deepseeksvc.com

function withQuery(url, k, v) {
  return url + (url.includes('?') ? '&' : '?') + k + '=' + v;
}

// type = 't' | 'p'
function defaultFmt(type) {
  // 浏览器支持 WebP 时不给 fmt，服务端默认吐 WebP；否则显式要 jpeg
  if (type === 't' || type === 'p') return supportsWebp() ? undefined : 'jpeg';
}

function buildImageUrl({ signedPath, type, fmt }) {
  if (!signedPath) return null;
  const base = /^https?:\/\//.test(signedPath)
    ? signedPath
    : `https://${FILES_BASE}${API_PREFIX}${signedPath}`;
  const out = withQuery(base, 'ty', type);
  const f = fmt ?? defaultFmt(type);
  return f ? withQuery(out, 'fmt', f) : out;
}
```

要点：

| 点 | 说明 |
|---|---|
| `signedPath` 是**带签名参数的路径**（已含 `?` 参数），所以拼 query 必须判断 `?` | 用 `withQuery` |
| `signedPath` 可能是绝对 URL | 直接透传，不拼域名 |
| 两个档位 | `ty=t`（thumbnail）/`ty=p`（preview）。**没有 `ty=o` 原图档** |
| 缓存 key | `type:fmt` 两段式（`"t:webp"` / `"p:jpeg"`），同一文件不同档位分开缓存 |
| 兜底降级链 | 目标档 → 兄弟档（`t`↔`p`）→ 本地 blob → 骨架 |

```js
const SIBLING = { t: 'p', p: 't' };   // 目标档加载失败时用另一档兜底

function resolveDisplayUrl(file, remoteType) {
  if (!(file.isImage && file.status === 'SUCCESS')) return { localOnly: true };
  const targetKey  = `${remoteType}:${fmtOf(remoteType)}`;
  const siblingKey = `${SIBLING[remoteType]}:${fmtOf(SIBLING[remoteType])}`;
  const rememberedTarget  = cache.get(file.id, targetKey);
  const rememberedSibling = cache.get(file.id, siblingKey);
  const generatedTarget   = buildImageUrl({ signedPath: file.signedPath, type: remoteType });
  return {
    targetRemoteUrl: rememberedTarget || generatedTarget,
    fallbackRemoteUrl: rememberedSibling || null,
    displayRemoteUrl: rememberedSibling || rememberedTarget || generatedTarget,
  };
}
```

「remembered」是为了**防止签名 URL 过期抖动**：只要某档 URL 成功 decode 过一次，就记住它，不再用新生成的（新生成的签名可能短暂不可用）。

---

## 5. 消息区图片样式（复刻重点）

### 5.1 布局容器

```css
/* 附件列表（消息内 + 输入区共用），eafda4ae */
.attachmentList {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;   /* 右对齐，跟随用户消息 */
  gap: 10px;
  position: relative;
}
```

消息内会覆盖内联样式：`{ margin-bottom: 0; max-height: none; position: relative; overflow: visible; pointer-events: auto|none }`
—— `overflow: visible` 很关键，否则图片 `::after` 描边与 hover 阴影会被裁掉。

**【推断·高置信】** 图片块位于**文本气泡上方**、右对齐；单图时不做「图文左右混排」。依据：容器 `justify-content:flex-end` + 消息侧渲染顺序（先 files 列表，再气泡）。

### 5.2 单图：240 基准等比盒（这是最值得抄的算法）

```js
const BASE = 240;          // 基准边长
const AR_MIN = 0.25;       // 最窄比例（1:4 竖图）
const AR_MAX = 4;          // 最宽比例（4:1 横图）

function computeSingleBox(naturalW, naturalH) {
  const ar = naturalW / naturalH;
  const displayAr = Math.max(AR_MIN, Math.min(AR_MAX, ar));
  if (ar >= 1) {                                  // 横图：宽锁定 240
    return { width: BASE, height: Math.round(BASE / displayAr) };
  }
  return { width: Math.round(BASE * displayAr), height: BASE };  // 竖图：高锁定 240
}

// 应用
containerStyle = { width: `${box.width}px`, height: `${box.height}px` };
imgStyle = {
  width: '100%', height: '100%', objectFit: 'cover',
  objectPosition: ar < 0.25 ? 'center top'     // 超长竖图：保留顶部（人脸/标题）
               : ar > 4    ? 'left center'     // 超宽横图：保留左侧
                           : 'center center',
};
```

效果约束：

- 宽高**都不会超过 240px**：横图 `height = 240/ar`（ar≥1 → height ∈ (60, 240]）；竖图 `width = 240*ar`（ar<1 → width ∈ [60, 240)）。
- 比例被钳制后仍然是 `object-fit: cover` 裁切（不是 contain），所以**看起来是「填满的方/竖/横块」**，不会有留白。
- 超长图用 `object-position` 保住信息区。

**尺寸未知时（本地还没测出宽高）**：容器直接 `240×240`，`img { max-width:100%; max-height:100%; width:auto; height:auto }` —— 即「contain 试摆」。

### 5.3 多图：64×64 方格

```css
/* 非单图模式：Ew = _2571442 */
.imageBox--grid {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 64px;
  height: 64px;
}
.imageBox--grid .img { object-fit: cover; width: 100%; height: 100%; }
```

**单图判定不是"图片数==1"，而是"该消息附件总数 == 1"**：

```js
const isSingleFile = files.length === 1;   // 含文档；只要还有 1 个 pdf，2 张图也会走 64 方格
```

并且档位随之切换：单图请求 `ty=p`（大图，因为要撑满 240），多图请求 `ty=t`（省流量）。

### 5.4 视觉细节（逐条都能对上）

```css
/* 外层盒子 cc804671 */
.imageBox {
  display: inline-block;
  position: relative;
  overflow: hidden;
  border-radius: 16px;
  background-color: var(--dsw-alias-markdown-placeholder);   /* 未加载时的底色 */
  line-height: 0;
  box-sizing: border-box;
  user-select: none;
  transition: background-color .2s ease-in-out;
}

/* 1px 内描边，不占布局、不吃圆角 */
.imageBox::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  box-shadow: inset 0 0 0 1px var(--dsw-alias-border-l2);
  pointer-events: none;
  z-index: 2;
}

/* 可点击 */
.imageBox--clickable { cursor: pointer; }

/* 图片本体 */
.imageBox .img { display: block; width: 100%; height: auto; transition: opacity .2s ease-in-out; }
```

**骨架 shimmer**（不是常见的一条扫光，而是一块旋转 135° 的模糊渐变）：

```css
/* hA = _3187ed9，注意尺寸 360×360 且 blur(30px) */
.shimmer {
  position: absolute;
  top: 50%; left: 50%;
  width: 360px; height: 360px;
  transform: translate(-50%, -50%) rotate(135deg);
  filter: blur(30px);
  pointer-events: none;
  z-index: 1;
  opacity: 1;
  background: linear-gradient(rgba(255,255,255,0) 40%, rgba(255,255,255,.8) 70%, rgba(255,255,255,0) 100%);
  animation: shimmerSweep 3s linear infinite;
  transition: opacity var(--duration) ease-in-out;
}
[data-ds-dark-theme] .shimmer {
  background: linear-gradient(rgba(255,255,255,0) 40%, rgba(255,255,255,.08) 70%, rgba(255,255,255,0) 100%);
}
```

**加载失败**：

```css
.errorOverlay {                    /* bf4f3529 */
  position: absolute; inset: 0; z-index: 1; pointer-events: none;
  display: flex; align-items: center; justify-content: center;
}
.errorIcon {                       /* _299318c */
  color: var(--dsw-alias-label-secondary);
  animation: .16s ease-out forwards iconFadeIn;   /* opacity 0 → 1 */
}
```

### 5.5 状态遮罩三种形态（很容易被忽略，但对比原站一眼能看出差别）

源码里：

```js
const overlayClass = overlayVariant === 'sentMessage'
  ? (single ? styles.sentGradient   /* DR = _5aa3f9b */
            : styles.sentMask)      /* NO = _50d572e */
  : styles.defaultMask;             /* Yr = _1e4da13 */
```

```css
/* 通用遮罩：满铺 + 半透明 + 圆角 */
.mask, .sentMask {
  position: absolute; inset: 0;
  display: flex; flex-direction: column;
  align-items: center; justify-content: flex-end;
  border-radius: 16px;
  background-color: var(--dsw-alias-bg-mask-3);
  pointer-events: none;
}

/* 已发送消息 + 单图：底部渐变，不做满铺 */
.sentGradient {
  position: absolute; left: 0; right: 0; bottom: 0;
  height: min(61px, 100%);
  display: flex; flex-direction: column;
  align-items: center; justify-content: flex-end;
  border-radius: 0 0 16px 16px;
  pointer-events: none;
  background: linear-gradient(180deg,
    transparent 0%,
    color-mix(in srgb, var(--dsw-static-neutral-1000) 65%, transparent) 100%);
}

/* 状态文案：最多 2 行 */
.statusText {
  font: var(--dsw-font-xxxs-strong-11);
  line-height: 14px;
  color: var(--dsw-static-neutral-00);
  text-align: center;
  margin: 0 8px 8px;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  white-space: normal;
  overflow: hidden;
}
.sentGradient .statusText { margin: 0 4px 8px; }
```

> 语义：单图大块用底部渐变条；多图小方块用整块半透明蒙层（小方块放不下渐变）。这是它比一般实现细致的地方。

### 5.6 加载时序（防闪烁）

```js
// 骨架：延迟 200ms 出现，最短展示 1200ms（loading 或 error 时强制最短时长）
const showSkeleton = useDelayedBool(loading, { showDelay: 200, minDuration: 1200, applyMinDuration: loading || error });

// 5 秒仍无任何可用 src → 停止骨架，退化为空占位（用户可点开重试）
useEffect(() => {
  if (!noSourceAtAll) return;
  const t = setTimeout(() => setGaveUp(true), 5000);
  return () => clearTimeout(t);
}, [noSourceAtAll]);
```

---

## 6. 输入区（发送前）上传卡片

### 6.1 结构

- 文件卡片（非图片）：`width: 240px; min-height: 64px; border-radius: 16px; border: 1px solid var(--dsw-alias-border-l2); background: var(--dsw-specific-input-major);`
  - 内部：左侧 28×28 图标位（`margin-right: 10px`）→ 文件名（`14px/500/22px`，单行省略）→ 副标题（`12px/15px`，状态文案，error 时 `--dsw-alias-state-error-primary`）。
  - `cursor: default`，可预览时变 `pointer` 并加 hover 背景 `--dsw-alias-interactive-bg-hover`。
- 图片卡片：复用同一套 240/64 图片盒，只是 `remoteImageUrlType` 固定为 `t`。
- 右下角删除按钮绝对定位（`position:absolute; bottom:0; right:0; padding-right:10px`），`visibility:hidden` → 父级 hover/聚焦时显示。

### 6.2 上传进度环（自己实现也很好抄）

```js
const R = 9, CIRC = 2 * Math.PI * R;      // 56.5487

<svg width="20" height="20" viewBox="0 0 20 20" fill="none" role="img" aria-label={`${Math.round(p)}%`}>
  <circle cx="10" cy="10" r="9" stroke="currentColor" strokeWidth="2" opacity="0.5" />
  <circle cx="10" cy="10" r="9" stroke="currentColor" strokeWidth="2"
          strokeDasharray={CIRC} strokeDashoffset={CIRC * (1 - p / 100)}
          transform="rotate(-90 10 10)" />
</svg>
```

进度值换算（源码里用 360° 表达再折百分比，等价于直接 percent）：

```js
const deg = 360 * clamp(percent01, 0, 1);
const pct = Math.round((clamp(deg, 0, 360) / 360) * 100);
```

完成态处理（很讲究，值得抄）：

```js
const completing = isUploading && percent >= 1;
const showDone = useDelayedBool(completing, { showDelay: 600, minDuration: 200 });
// 环形淡出 50ms，然后换成转圈 loading；percent<1 时继续显示环
```

状态机（`mode`）：

| mode | 触发 |
|---|---|
| `normal` | 默认 |
| `loading` | 上传中 / 解析中 |
| `retryableError` | 可重试失败（点击重试） |
| `fatalError` | 不可重试（点击删除） |
| `contentFiltered` | 内容违规（禁止发送，必须先删） |
| `auditUnknown` | `SUCCESS` 但 `audit_result === 'unknown'` |

有 `contentFiltered` 或 `fatalError` 时，**发送按钮被禁用**并提示「请删除异常文件再发送」。

---

## 7. 大图查看器 `FancyBox`

### 7.1 结构

```jsx
createPortal(
  <div className="ds-fancy-box" style={{ '--fancy-box-thumbnails-height': showThumbnails ? '96px' : '0px' }}>
    <div className="bg-mask" ref={maskRef} />
    <TopToolbar />                        {/* 右上：关闭（可外部注入按钮） */}
    <SideToolbar />                       {/* 左右居中：上一张/下一张，<768px 隐藏 */}
    <SlideStack />                        {/* 当前图 + 过渡中的上一张 */}
    <Thumbnails />                        {/* 底部条，可选 */}
  </div>,
  document.body
);
```

```css
.ds-fancy-box { position: absolute; inset: 0; width: 100%; height: 100%; z-index: 2000; }
.ds-fancy-box .bg-mask {
  position: absolute; inset: 0;
  background-color: rgba(0, 0, 0, .88);
  backdrop-filter: blur(2px);
}

.ds-fancy-box__top-toolbar {
  position: absolute; top: 12px; right: 12px;
  display: flex; align-items: center; gap: 9px;
  color: #fff; z-index: 2001;
}
.ds-fancy-box__top-toolbar .ds-button__background { backdrop-filter: blur(5px); }

.ds-fancy-box__side-toolbar {
  position: absolute; top: 50%; left: 50%;
  transform: translate(-50%, -50%);
  width: 100%; padding: 0 24px; box-sizing: border-box;
  display: flex; align-items: center; justify-content: space-between;
  color: #fff; pointer-events: none; z-index: 2001;
}
.ds-fancy-box__side-toolbar .ds-button { pointer-events: auto; }
@media screen and (max-width: 768px) { .ds-fancy-box__side-toolbar { display: none; } }

.ds-fancy-box__slide {
  position: absolute; inset: 0;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
}
.ds-fancy-box__slide.with-thumbnails { height: calc(100% - var(--fancy-box-thumbnails-height, 0)); }
.ds-fancy-box__slide .content { transform-origin: 0 0; will-change: transform; user-select: none; z-index: 2; }
.ds-fancy-box__slide .content .image { width: 100%; height: 100%; user-drag: none; -webkit-user-drag: none; }
.ds-fancy-box__slide .caption {
  color: #fff; font-size: 14px; line-height: 1.5; padding: 12px;
  text-align: center; visibility: hidden; flex-shrink: 0; min-height: 0;
}
.ds-fancy-box__slide .caption.visible { visibility: visible; }
```

### 7.2 尺寸自适应

```js
// 位图：容器够大就按原尺寸显示，否则等比缩到容器内（caption 高度要扣掉）
function fitImage(natural, cw, ch) {
  if (ch >= natural.height && cw >= natural.width) return natural;
  const ar = natural.width / natural.height;
  return natural.height / ch > natural.width / cw
    ? { height: ch, width: ch * ar }        // 高受限
    : { height: cw / ar, width: cw };       // 宽受限
}

// SVG：始终占容器的 90%（留边）
function fitSvg(natural, cw, ch) {
  const ar = natural.width / natural.height;
  const w = 0.9 * cw, h = 0.9 * ch;
  const box = { width: w, height: w / ar };
  return box.height <= h ? box : { width: h * ar, height: h };
}
```

拿到尺寸后直接把 `content` 的 `style.width/height` 写成 px（不走 CSS max-width），这样 `transform: scale()` 的原点计算才稳定。

### 7.3 缩放

```js
const MIN = 1;
// 上限随「原图/显示尺寸比」自适应：小图允许放得更大，避免 4x 仍旧很小
const maxScale = () => (!naturalSize || naturalToContentScale <= 0)
  ? 4
  : Math.max(4, 2 / naturalToContentScale);      // naturalToContentScale = 显示宽 / 原图宽

const clampScale = s => Math.min(Math.max(s, MIN), maxScale());
```

- **滚轮**：`scale *= 1 - deltaY * (ctrlKey ? 0.005 : 0.0005)`，再 clamp。
  （`ctrlKey` 分支是为触控板捏合一类事件准备的，步长放大 10 倍。）
- **单击**（`mode: 'auto'`）：
  ```js
  if (scale > 1) return 1;                                  // 已放大 → 回归适配
  if (winH / contentH > 1.1 || winW / contentW > 1.1)        // 图比窗口小
    return clampScale(Math.max(winH / contentH, winW / contentW));  // 放大到铺满
  return clampScale(1.6 * scale);                            // 否则 1.6x
  ```
- **以点击点为锚点**（关键，否则会「越缩越偏」）：
  ```js
  const x = clickX / scale * (scale - newScale) + x;
  const y = clickY / scale * (scale - newScale) + y;
  ```
- 缩放动画：`transition: transform .4s`；并且有 500ms 的 `zooming` 锁，防连点抖动。
- `mode: 'in' | 'out'` 分别是 `×1.6` / `×0.5`（工具栏按钮用）。

### 7.4 拖拽 + 惯性

- 拖动只在放大后生效（`scale > 1`），位移直接叠加到 `translate`。
- 边界约束（`adjustPos`）：
  - 图比容器**小**时：`y = 0.5 * containerH * (1 - scale)`（居中）；
  - 图比容器**大**时：`y ∈ [-(h - containerH)/2, +(h - containerH)/2]`。
- 惯性参数（`inertiaDamp`）：
  ```js
  { decelerationFactor: 0.15, durationFactor: 100,
    minDuration: 0.05, maxDuration: 0.5, minSpeedThreshold: 500 }
  ```

### 7.5 切图（左右滑）

```js
// 只有 scale === 1 时允许滑动切图（放大后拖拽是「看图」，不是「翻页」）
shouldEnableSlide() { return this.scale === 1; }

// 方向判定：位移超过 8px 才定性
if (max(|dx|, |dy|) >= 8) direction = |dx| > 1.2 * |dy| ? 'horizontal' : 'vertical';

// 松手：水平位移 > 20px 才真正翻页
if (|sliding| > 20) sliding > 0 ? toPrev() : toNext();
```

过渡：`slide-fade-in` / `slide-fade-out` 各 `0.2s`，同时把**上一张**留在 DOM 里（`[..new Set([...history, current])]`），避免切换瞬间黑屏。

循环逻辑很讲究：如果图片带 `previewOrder`，会按 order 找「相邻的下一个/上一个」，找不到就回绕到首/尾；否则按数组顺序 `(T + delta + len) % len` 循环。

### 7.6 键盘

```js
// 捕获阶段监听，避免被编辑器/弹窗吃掉
document.addEventListener('keydown', handler, true);

Escape                        → close()
ArrowLeft | ArrowUp           → prev()
ArrowRight | ArrowDown        → next()
```

同时打开时会：
- `document.addEventListener('wheel', preventDefault, { passive: false })` 且 **屏蔽 `ctrlKey` 的默认缩放**（防浏览器整页缩放）；
- `document.addEventListener('touchmove', preventDefault, { passive: false })` 防背景滚动。

### 7.7 底部缩略图条

```css
.ds-fancy-box__thumbnails {
  position: absolute; bottom: 0; width: 100%;
  height: var(--fancy-box-thumbnails-height);    /* 96px */
  padding: 9px 0; box-sizing: border-box;
  display: none;
}
.ds-fancy-box__thumbnails.visible { display: flex; align-items: center; justify-content: center; }
.ds-fancy-box__thumbnails .image-wrap { max-width: 100%; height: 100%; overflow-x: auto; overflow-y: hidden; white-space: nowrap; }
.ds-fancy-box__thumbnails .image {
  width: 50px; height: 100%; margin: 0 8px;
  object-fit: cover; border-radius: 6px; cursor: pointer;
  transition: width .25s;          /* 当前项 110px，靠宽度动画做「聚焦」 */
  user-select: none;
}
.ds-fancy-box__thumbnails .image.current { width: 110px; }
```

一句话：**缩略图靠"当前项变宽"来指示选中，不做描边/遮罩**，动画 250ms。

### 7.7.1 只渲染「当前 + 最近历史」

```js
[...new Set([...history, currentIndex])].map(i => <Slide key={previewKey} ... />)
// 动画结束（onAnimationEnd）后把退出项从 history 移除
```
好处：切 50 张图时 DOM 里最多 2~3 个 `<img>`。

### 7.8 三类图源（一个查看器吃三种内容）

```ts
type FancyImage =
  | { type: 'image';       src: string; previewKey: string; previewOrder?: number; theme?: 'light'|'dark' }
  | { type: 'fetch-image'; key: string;  previewKey: string; fetcher: () => Promise<string>; isSvg?: boolean }
  | { type: 'svg';         svgDataUri: string; svgHtml: string; theme?: 'light'|'dark' };
```

- `fetch-image` 用于**需要鉴权/跨域取文本**的图（例如 SVG）：先 `fetch(url).then(r => r.text())`，若判定是 SVG，则把 innerHTML 塞进 `<svg>` 并转成 data URI；否则降级成普通 `image`。
- SVG 分支有单独的 `svg-wrapper` 与 `theme_light / theme_dark` 背景色，且「惯性拖拽」可被 `disableSvgInertia` 关掉（SVG 交互多，拖拽会打架）。
- 内容裁剪/审核不通过的图片，在打开前就被过滤掉：

```js
if (file.auditResult === 'reject' || isContentFiltered(file)) return null;   // 不进查看器
```

### 7.9 其它实证细节

| 行为 | 实现 |
|---|---|
| 图片原始尺寸 | 渲染一个 `display:none` 的 `<img>` 量 `naturalWidth/Height`，结果按 src 缓存在 `Map` 里（跨次打开复用） |
| 加载失败判定 | `onError` 后 **300ms** 才置为 fail（避免瞬时网络抖动直接报错） |
| 失败态 UI | 居中的图标 + 「似乎出了点问题，重新加载」文字，点击重试（`clickRetry` 埋点） |
| 点遮罩关闭 | 只有当 `event.target === slideEl` 才算点遮罩；触摸时位移 < 10px 才算 tap |
| 防误关 | `mousedown` 和 `click` 的 target 都要等于 slide（防"按下图、松手在遮罩"误关） |
| 右键/拖拽 | `user-drag: none`、`user-select: none`，避免拖出 ghost image |
| 图片列表同步 | 父级用 `sourceKey` + 序列化签名（`[previewOrder,id,status,auditResult,signedPath,fileName,isImage,displayUrl,theme]`）比对，判断是否需要 `syncImages` |
| 打开埋点 | `fancyBoxReveal`；失败埋点 `fancyBoxLoadFail` / `fancyBoxFetchImageSrcFail` / `fancyBoxClickRetry` |

**没有下载按钮**：默认 `topToolbarButtons` 只有关闭（`hQ = [关闭]`），图片查看器里未发现「下载原图」入口。这是它与很多同类产品的差异点，复刻时按需加。

---

## 8. 复刻落地方案

### 8.1 组件拆分（推荐）

```
<MessageAttachments files={files}>            // 容器：flex-wrap / gap 10 / justify-end
  ├─ <ImageTile file single />                // 240 基准盒 或 64 方格
  │    ├─ <Shimmer />                        // 360×360 blur30 旋转渐变
  │    ├─ <img style={imgStyle} />
  │    ├─ <StatusOverlay mode variant />      // sentGradient / sentMask / defaultMask
  │    └─ <ErrorIcon />
  └─ <FileCard file />                       // 240×64 文档卡片
</MessageAttachments>

<ImageViewerProvider>                        // 全局唯一，portal 到 body
  <FancyBox images apiRef />
</ImageViewerProvider>
```

同一份 `<ImageTile>` 用 `single` 与 `remoteType` 两个 prop，同时服务「输入区」和「消息区」：

| 场景 | single | remoteType | 点击行为 | 状态覆盖层 |
|---|---|---|---|---|
| 输入区（未发送） | 附件数==1 | 始终 `t` | 打开查看器 | 上传进度环 + 状态文案 |
| 消息区 | 附件数==1 | `single ? p : t` | 打开查看器 | 状态文案（已发送样式） |

### 8.2 核心代码骨架

**① 尺寸计算**

```ts
export const IMAGE_BOX_BASE = 240;
const AR_MIN = 0.25, AR_MAX = 4;

export function computeImageBox(w: number, h: number) {
  const ar = w / h;
  const dar = Math.min(AR_MAX, Math.max(AR_MIN, ar));
  const box = ar >= 1
    ? { width: IMAGE_BOX_BASE, height: Math.round(IMAGE_BOX_BASE / dar) }
    : { width: Math.round(IMAGE_BOX_BASE * dar), height: IMAGE_BOX_BASE };
  const objectPosition = ar < AR_MIN ? 'center top' : ar > AR_MAX ? 'left center' : 'center center';
  return { ...box, objectPosition };
}

export function tileStyle(single: boolean, w?: number, h?: number) {
  if (!single) {
    return {
      container: { width: 64, height: 64 },
      img: { width: '100%', height: '100%', objectFit: 'cover' as const },
    };
  }
  if (w && h) {
    const b = computeImageBox(w, h);
    return {
      container: { width: b.width, height: b.height },
      img: { width: '100%', height: '100%', objectFit: 'cover' as const, objectPosition: b.objectPosition },
    };
  }
  return {
    container: { width: IMAGE_BOX_BASE, height: IMAGE_BOX_BASE },
    img: { maxWidth: '100%', maxHeight: '100%', width: 'auto', height: 'auto' },
  };
}
```

**② 图片瓦片**

```tsx
function ImageTile({ file, single, onOpen, variant, statusText }: Props) {
  const { localThumbnailUrl, displayRemoteUrl, imageError, handleRemoteLoaded } = useImageSource(file, single ? 'p' : 't');
  const [showingRemote, setShowingRemote] = useState(false);
  const src = showingRemote ? displayRemoteUrl ?? localThumbnailUrl : localThumbnailUrl ?? displayRemoteUrl;

  // blob → 远端：先 decode 预热，成功再切（防白闪）
  useEffect(() => {
    if (!displayRemoteUrl || showingRemote) return;
    let cancelled = false;
    preloadImage(displayRemoteUrl).then(ok => {
      if (!cancelled && ok) { setShowingRemote(true); handleRemoteLoaded(displayRemoteUrl); }
    });
    return () => { cancelled = true; };
  }, [displayRemoteUrl, showingRemote]);

  const { container, img } = tileStyle(single, file.width, file.height);
  return (
    <div className={cx('imageBox', single && 'imageBox--single', !single && 'imageBox--grid')}
         style={container}
         role="button" tabIndex={0}
         onClick={() => onOpen(file)}
         onKeyDown={e => e.key === 'Enter' && onOpen(file)}>
      {!src && <Shimmer />}
      {src && <img className="img" src={src} style={img} alt={file.fileName}
                   onLoad={/* 清除本地 blob */} onError={/* 走 fallback 链 */} />}
      {statusText && <StatusOverlay variant={variant} text={statusText} />}
      {imageError && <ErrorIcon />}
    </div>
  );
}
```

**③ 查看器核心（不复刻它的 className 体系，只复刻数学）**

```tsx
function useZoomStage(contentRef: RefObject<HTMLElement>, naturalRef: MutableRefObject<Size|null>) {
  const [state, setState] = useState({ scale: 1, x: 0, y: 0 });
  const ratioRef = useRef(1);      // 显示宽 / 原图宽

  const maxScale = () => (!naturalRef.current || ratioRef.current <= 0)
    ? 4 : Math.max(4, 2 / ratioRef.current);
  const clamp = (s: number) => Math.min(Math.max(s, 1), maxScale());

  const apply = (next: { scale: number; x: number; y: number }, animated = false) => {
    const el = contentRef.current!;
    el.style.transition = animated ? 'transform .4s' : 'none';
    el.style.transform = `translate(${next.x.toFixed(4)}px, ${next.y.toFixed(4)}px) scale(${next.scale.toFixed(4)})`;
    setState(next);
  };

  const zoomAt = (clientX: number, clientY: number, target: number, animated = false) => {
    const rect = contentRef.current!.getBoundingClientRect();
    const clickX = clientX - rect.left, clickY = clientY - rect.top;
    const next = clamp(target);
    if (next === state.scale) return;
    apply({
      scale: next,
      x: (clickX / state.scale) * (state.scale - next) + state.x,
      y: (clickY / state.scale) * (state.scale - next) + state.y,
    }, animated);
  };

  const onWheel = (e: WheelEvent) => {
    e.preventDefault();
    zoomAt(e.clientX, e.clientY, state.scale * (1 - e.deltaY * (e.ctrlKey ? 0.005 : 0.0005)));
  };

  const onClick = (e: React.MouseEvent) => {
    if (state.scale > 1) return zoomAt(e.clientX, e.clientY, 1, true);
    const c = contentRef.current!.getBoundingClientRect();
    if (window.innerHeight / c.height > 1.1 || window.innerWidth / c.width > 1.1)
      zoomAt(e.clientX, e.clientY, Math.max(window.innerHeight / c.height, window.innerWidth / c.width), true);
    else
      zoomAt(e.clientX, e.clientY, state.scale * 1.6, true);
  };

  return { state, apply, onWheel, onClick, setRatio: (r: number) => (ratioRef.current = r) };
}
```

**④ 设计变量映射**（换皮肤只需改这一层）

| DeepSeek 变量 | 语义 | 建议值（亮/暗） |
|---|---|---|
| `--dsw-alias-markdown-placeholder` | 图片未加载底色 | `#f4f5f7` / `#26282c` |
| `--dsw-alias-border-l2` | 1px 内描边 | `rgba(0,0,0,.06)` / `rgba(255,255,255,.08)` |
| `--dsw-alias-bg-mask-3` | 状态蒙层 | `rgba(0,0,0,.45)` |
| `--dsw-static-neutral-1000` | 渐变底色 | `#000` |
| `--dsw-static-neutral-00` | 图上白字 | `#fff` |
| `--dsw-specific-input-major` | 输入区卡片底 | `#fff` / `#2b2d31` |
| `--dsw-font-xxxs-strong-11` | 11px 加粗 | `500 11px/14px` |

### 8.3 复刻 checklist

- [ ] 本地秒显：`URL.createObjectURL` + 测 naturalWidth/Height，**不等上传**
- [ ] 上传中断/删除：`AbortController`
- [ ] 上传成功不立刻换图：`img.decode()` 预热再切
- [ ] 单个 `file` 字段 + 三个自定义头（若你的后端照抄）
- [ ] `signedPath` 两档 URL + `t/p` 兄弟档兜底
- [ ] `signedPath` 可能是绝对 URL，拼参数要判断 `?`
- [ ] 单图 240 盒 / 多图 64 方格 / 判定条件是**附件总数**
- [ ] `border-radius: 16px` + `::after` `inset 0 0 0 1px` 内描边
- [ ] 骨架 200ms 延迟 + 1200ms 最短 + 5s 放弃
- [ ] 多图用 64px，超长图用 `object-position: center top / left center`
- [ ] 输入区进度环 `r=9 / stroke=2 / -90° / dasharray 56.55`
- [ ] 查看器：`z-index 2000` + `rgba(0,0,0,.88)` + `blur(2px)`
- [ ] 查看器：`clamp(1, max(4, 2/ratio))`、滚轮系数 `0.0005/0.005`、点击 `1.6x`
- [ ] 查看器：缩放锚定点击点（不要用 `transform-origin: center` 糊过去）
- [ ] 切图条件 `scale === 1` + 位移阈值 20px + 方向阈值 8px/1.2
- [ ] 捕获阶段监听键盘 + `wheel`/`touchmove` 非被动阻止

---

## 9. 与 DeepSeek 的差异点 / 风险提示

1. **签名 URL 会过期**。DeepSeek 用「remembered URL」缓解：成功后记住，不重新生成。复刻时若每次渲染都重新签名，历史消息滚动后会整片重载/闪烁。
2. **`ty=p` 不是原图**，只是更大的一档。查看器里「放到 4x 变模糊」是设计内的。想给用户原图必须后端加 `ty=o`。
3. **图片审核是同步门禁**：`audit_result === 'reject'` 的图片无法进入查看器，且在输入区就禁止发送。若你的产品没有审核，`statusText` / `contentFiltered` 这套可以直接砍掉，但**不要砍 `::after` 内描边和 16px 圆角**，那是「像 DeepSeek」的主要视觉来源。
4. **`enable_thumbnail` 是服务端开关**。图片被当普通文件渲染时是 240×64 的文档卡片，不是破图 —— 复刻时要区分「图片加载失败」和「后端不允许展示缩略图」。
5. **多图 64×64 很小**。超过 8 张会显得拥挤（`flex-wrap` + `gap:10`，靠换行），但每张仍 64。想更好可以自适应（如 3 张时 88px），但那就和原站不一样了。
6. **查看器是 `position: absolute` 而不是 `fixed`**，依赖 `body` 无 transform 祖先；如果你的应用在某个有 `transform` 的容器里 portal，记得改 `fixed`。
7. **它没有「下载原图」**。复刻如果要加，注意跨域：`files.*` 域名需要 `Access-Control-Allow-Origin` 才能 `fetch → blob → a[download]`，否则只能 `window.open` 让浏览器原生处理。

---

## 10. 待验证项（需要登录态才能坐实）

| 项 | 为什么没法静态确认 |
|---|---|
| 上传接口是否还有 POW 头 | 代码里 `create_pow_challenge` 只为 `completion_like` / `upload_file` 两个 scene 服务，但上传路径没看到挂 `x-ds-pow-response`（可能在 http 拦截器里统一注入），**建议抓一次真实请求确认** |
| `ty=t` 的实际像素尺寸 | 服务端行为，前端只知道「列表/多图用它」 |
| `max_input_file_count` 线上真实值 | 服务端下发，前端兜底 50 |
| 图片是否走独立 CDN 域名/防盗链 | 未在产物中找到 referer 校验逻辑 |
| 是否有「长按保存」移动端行为 | 移动端代码有 `isMobile` 分支，但未找到图片保存动作 |

抓一次真实请求（F12 → Network → 上传一张 png）就能把第 1 项补完：

```
POST /api/v0/file/upload_file
看 Request Headers 里有没有额外的 x-ds-* / authorization 之外的自定义头
看 Response 里 biz_data.signed_path 的形态（是否 /files/xxxx?x-oss-signature=...）
```

---

## 附录 A：关键符号索引（在 `main.d1d163b7e4.js` 中的位置）

| 偏移 | 内容 |
|---|---|
| ~357,300 | `l1 = {targetWidth:128, targetHeight:128, sizeThreshold:192}` 本地缩略图参数 |
| ~357,400 | `files.deepseeksvc.com` / `buildImageUrl`(`dp`) / `withQuery`(`dc`) |
| ~359,900 | `imageInfoMap` store（`localThumbnailUrl` / `stableRemoteUrlMap`） |
| ~383,400 | `preloadImage`(`d5`)、并发 4 |
| ~385,600 | `doUploadFile` 主体（headers / progress / 状态迁移） |
| ~391,300 | `fetchFilesInfoOnce`（轮询 `/api/v0/file/fetch_files`） |
| ~417,800 | `POST /api/v0/file/upload_file` 常量 + `create_pow_challenge` |
| ~447,800 | 惯性/拖拽/切图（`hw`）与 fit 计算（`hE`/`hS`） |
| ~457,900 | `clampScale` 使用点、滚轮、点击缩放（`hy`） |
| ~467,900 | `FancyBox` 组件族（`hF/h1/h2/h4/hq/hJ/h0`） |
| ~535,000 | 消息内图片组件（`gu` / `gp` / `gg`） |
| ~545,500 | 附件列表容器（`gR`）、文档卡片（`gO`） |
| ~559,800 | 上传进度环（`gJ` / `g2` / `g3`） |
| ~1,030,000 | CSS Module 类名映射表 |

## 附录 B：CSS 类名 → 实际样式 对照表

| 代码里的键 | hash 类名 | 用途 |
|---|---|---|
| `gh.kL` | `cc804671` | 图片盒（16 圆角 / 底色 / `::after` 描边） |
| `gh.vk` | `_4037039` | 可点击 `cursor:pointer` |
| `gh.Ew` | `_2571442` | 非单图 64×64 |
| `gh.Sl` | `_9f130b7` | `<img>` 本体 |
| `gh.hA` | `_3187ed9` | 骨架 shimmer |
| `gh.E4` | `bf4f3529` | 错误图标容器 |
| `gh.ik` | `_299318c` | 错误图标颜色 + 淡入 |
| `gm.To` | `ab589b02` | 附件行（单图，min-height 64） |
| `gm.nk` | `aa6d68c8` | 附件行（多图） |
| `gm.Yr` | `_1e4da13` | 默认满铺遮罩 |
| `gm.NO` | `_50d572e` | 已发送 + 多图满铺遮罩 |
| `gm.DR` | `_5aa3f9b` | 已发送 + 单图底部渐变 |
| `gm.qS` | `_04f3d3b` | 状态文案（2 行截断） |
| `gx.BQ` | `eafda4ae` | 附件列表容器（wrap / gap10 / flex-end） |
| `gx.qs` | `_25c7358` | 文档卡片 240×64 |
| `gx.hG` | `cd314545` | 文档卡片内层 |
| `gx.Yg` | `_1c3b90b` | 文件图标 28×28 |
| `gx.mh` | `dafb6286` | 可预览 hover 态 |
| `gx.Qs` | `_158cea4` | 文件名区 |
| `gx.pG` | `e70accd6` | 文件名文本 |
| `gx.X1` | `_7103a25` | 副标题 / 状态文本 |
