# DeepSeek 网页「快」与「顺」的逆向调研

调研对象：`https://chat.deepseek.com/`（发布包 commit-id `181415e6`）
调研日期：2026-10-23（Asia/Shanghai）
产出目的：解释「为什么响应快、为什么滚动/流式输出不卡」，并给出本项目的可复刻清单。

---

## 0. 一句话结论

DeepSeek 的「快」**不是**靠 SSR、不是靠 Service Worker、也不是靠 React 18 并发特性，而是四件事叠起来：

1. **首屏只下载 3 个文件**（2 JS + 1 CSS，brotli 后 786 KB），其余 606 个分包按需加载（实测登录页会额外提前拉一次 katex，见 §2.3）；
2. **静态资源 30 天 immutable + 边缘命中**，复访实测网络传输只有 **9.9 KB**（对比首访 1.30 MB）；
3. **把等待藏进用户还没动作的时间**：进页面就预创建对话 session、登录后就预解 PoW 挑战、启动 5 秒后预取 TTS 解码器；
4. 渲染侧**自己写了一套可变高度虚拟列表 + 面向流式的增量 Markdown 渲染器**，让「每个 token 到达」都不触发全量重排。

登录态实测（真实账号、100 条历史会话、144 Hz 屏）把这一条量化了：滚动消息区 **p50 6.9 ms / p95 7.3 ms、136 fps**，1,600 ms 里只有 2 帧超过 33 ms；唯一的明显停顿是点开会话时的 **186 ms** 长任务，之后立刻恢复平滑（§2.4）。

---

## 1. 范围、方法与证据等级

### 1.1 本次实际做了什么

| 手段 | 说明 |
| --- | --- |
| 线上产物抓取 | 抓 HTML / 2 个 JS / CSS / manifest，brotli 解压后静态分析（共 2.64 MB JS + 298 KB CSS） |
| 分包表还原 | 从 webpack 运行时的 `h.u` 函数还原出**全部 606 个异步 chunk** 的 id→名称→哈希 |
| 真实浏览器实测 | 用 CDP 驱动本机 Chrome 导航两次，采集 Navigation Timing、Resource Timing、FCP/LCP/CLS、longtask、DOM 规模 |
| **登录态实测** | 独立 Profile + CDP 复用真实账号，量登录后的会话列表、打开历史会话、程序化滚动帧率（见 §2.4） |
| 传输层探测 | TLS 握手 / ALPN / 证书、响应头、CDN 缓存头、字体与分包体积 |

留档文件见 `F:\project\dswui\.workbuddy\report-deepseek\`（`runtime2.json`、`runtime_auth.json`、`cache_test.json`、`api_probe.txt`、`chunk_table.tsv`、`ctx*.txt`）。

### 1.2 证据等级约定

- **【实证】**：有响应头、字节数、时间戳、DOM 或反编译代码原文佐证。
- **【静态确认】**：代码里确实存在该逻辑，但未在登录态下触发验证。
- **【推断】**：由代码结构推得的设计意图，标注为推断。

### 1.3 明确的边界

- §2.1 / §2.2 的实测对象是**未登录的登录页**；§2.4 已补上**登录态**（真实账号、100 条历史会话）的导航、渲染与滚动实测。
- 未抓取真实对话 SSE 流量（发消息会产生费用与历史记录），因此**不宣称**首字延迟数值；流式渲染的结论仍是发布包静态分析 + 登录态 DOM 观察。
- 滚动帧率取决于本机屏幕刷新率（本次为 144 Hz）与 GPU，绝对值不可直接跨机器比较，应看 p95 与 >33 ms 掉帧数。

> 本文与既有文档的关系：`docs/deepseek-ui-analysis-2026-10-22.md` 已给出交互与结构的定性观察，并明确说明「未录制 Performance trace、未做首字延迟/内存压测」。本文补上**可复现的量化实测**与**逐机制拆解**，两者不冲突。

---

## 2. 实测数据

### 2.1 冷启动 vs 复访（同一次 Chrome 会话，连读两次）

| 指标 | 第 1 次（冷） | 第 2 次（热） | 说明 |
| --- | --- | --- | --- |
| HTML TTFB | 163 ms | **70 ms** | 走 TLS1.3 + HTTP/2 |
| DOMContentLoaded | 529 ms | **250 ms** | |
| load 事件 | 3058 ms | — | 主要被第三方 SDK 拖尾 |
| 网络传输总量 | **1,303,722 B** | **9,878 B** | 27 个资源里 16 个 `transferSize = 0` |
| FCP / LCP | 1660 ms / 1660 ms | — | 登录页（含风控/微信/Apple SDK） |
| CLS | 0 | — | 全程零布局抖动 |
| Long Task | 2 条：147 ms、304 ms | — | 见 §5「代价」 |

复访时 `main.js / default-vendors.js / main.css / woff2 / katex` **全部 `transferSize=0、duration=0`**，即磁盘缓存直出。

### 2.2 首屏资源真实体积（brotli）

| 资源 | 线上传输 | 解压后 | 缓存策略 |
| --- | --- | --- | --- |
| `index.html` | 4,046 B | 8,549 B | `no-store` |
| `default-vendors.a2a242fbd2.js` | 309,611 B | 1,033,287 B | `max-age=2592000` |
| `main.85762178e1.js` | 474,979 B | 1,608,989 B | `max-age=2592000` |
| `main.cc59a63cb5.css` | 47,999 B | 298,333 B | `max-age=2592000` |
| `Inter-Regular-subset…woff2` | 28,068 B | — | `max-age=2592000` |

CDN 边缘实测命中：响应头 `age` 达 800,880 / 3,891,004 秒，`server: openresty`；业务域 `server: CW`，TLS 均为 **TLS1.3 / TLS_AES_256_GCM_SHA384**，ALPN 协商 **h2**。【实证】

### 2.3 按需加载的真实价格（这些都没进首屏）

| 分包 | 名称 | 传输体积 | 触发时机 |
| --- | --- | --- | --- |
| 52059 | `katex` (+ css 4 KB) | 84,178 B | 渲染公式 |
| 96259 | `mermaid-vendors` | 236,603 B | 渲染 mermaid 图 |
| 32549 | `prism-common` | 44,936 B | 代码高亮 |
| 27390/70028/81800/97548 | `prism-a-d/e-l/m-r/s-z` | 分四段 | 按代码语言字母段懒加载 |
| 91851 | `previewAll` | 107,994 B | HTML/网页预览 |
| 61271 | `opus-decoder-wasm` | 76,566 B + wasm 94,701 B | 语音播放 |
| 74268 | `web-tts` | 11,342 B | 语音合成 |

**11 个具名分包 + 595 个数字分包，全部不在首屏声明链路内。**

⚠️ 一处需要如实说明的例外：实测登录页在 45 ms 内就加载了 `katex.js`（84 KB）+ `katex.css`（4 KB），说明 KaTeX 要么被登录页的富文本组件直接引用，要么被提前预取；`mermaid` / `prism` / `preview` / `opus-wasm` 则在本次登录页会话中确实未随首屏加载（后者由 5 秒定时器预取触发）。这与既有文档「首屏已发现 KaTeX CSS」的观察一致。

### 2.4 登录态实测：真实工作负载（侧边栏 100 条历史 + 打开历史会话）

§2.1 测的是**登录页**，看不到虚拟列表和 Markdown 渲染 —— 而那才是「丝滑」的主战场。为此用独立 Chrome Profile（`.workbuddy/chrome-profile`）承载真实登录态，CDP 接入后在同一实例里开新标签页导航，量到的是登录后的应用本体。【实证】

| 指标 | 已登录 · 新建对话页 | 点开一条历史会话后 |
| --- | --- | --- |
| HTML TTFB | 164 ms | — |
| DOMContentLoaded | 316 ms | — |
| load 事件 | 453 ms | — |
| FCP | 492 ms | 492 ms（外壳早已出现） |
| LCP | 492 ms | **6192 ms**（LCP 被后渲染的消息内容刷新） |
| CLS | 0 | 0 |
| Long Task | 3 条：114 / 81 / 57 ms | **+1 条：186 ms（@5910 ms）** |
| DOM 节点 | 1,424 | 3,242 |
| `.ds-virtual-list` | 0 | 1（本次只挂 4 个节点） |
| `.ds-markdown` | 0 | 5 |
| JS 堆 | 36.6 MB | 39.3 MB |
| 应用外壳就绪 | **976 ms** | — |

滚动实测（1,600 ms 程序化滚动，144 Hz 屏，rAF 采样）：

| 滚动区域 | 帧数 / 帧率 | p50 | p95 | >16.7 ms | >33 ms |
| --- | --- | --- | --- | --- | --- |
| 侧边栏会话列表（可滚 3,408 px） | 174 帧 / 109 fps | 7.2 ms | 18.2 ms | 13 | 2 |
| 会话消息区（可滚 7,410 px） | 217 帧 / 136 fps | **6.9 ms** | **7.3 ms** | 2 | 2 |

即：**滚动过程中的帧间隔贴着刷新率（6.9 ms ≈ 144 Hz），没有连续掉帧**。整场唯一可感知的停顿是「点开会话」那一下的 186 ms 长任务（拉取会话 + 首屏渲染消息 + 布局），之后立刻恢复平滑。这正好印证 §4.1 的虚拟列表设计：打开会话时才创建 `.ds-virtual-list`，且只挂载可视区内的节点。

网络画像（本次导航 67 个网络可见资源 / 202,017 B 传输 / 21 个 `encodedDataLength = 0` 即缓存直出）：

| 类别 | 数量 | 观察 |
| --- | --- | --- |
| 静态资源（JS / CSS / 字体） | 9 / 2 / 5 | **全部 `disk` 缓存命中，传输 0 字节** |
| 业务 API（`/api/v0/*`） | 17 | 最慢：`users/current` 345 ms、`auth_token/check_device` 461 ms、`client/settings/report` 219 ms |
| 埋点（`gator.volces.com/list`） | **20** | 比业务 API 还多，单条 50–330 ms，全部异步不阻塞渲染 |
| 会话列表分页 | 4 | `chat_session/fetch_page` 73 ms / 5.6 KB，随后 3 次翻页各 5.6 KB —— 列表是分页拉的，不是首屏全量 |
| 失败请求 | 4 | `hif-dliq.deepseek.com/query` 全部 `ERR_CONNECTION_CLOSED`（远程特性开关，失败后静默降级） |

**顺带确认了一个容易踩坑的事实：DeepSeek 的登录态不在 cookie 里。**

- `localStorage.userToken` 存的是 appKit 包装过的 JSON：`{"value":"<64 字符 token>"}`，请求时取 `.value` 放进 `Authorization: Bearer <token>`。
- 不带 token 调 `/api/v0/users/current` 返回 `{"code":40002,"msg":"Missing Token"}`；带错 token 返回 `40003 Authorization Failed`。
- cookie 里只有 WAF / 风控项（`HWWAFSESID`、`HWWAFSESTIME`、`smidV2`、`.thumbcache_*`），**没有任何 session cookie**。

所以「用已登录页面做实测」不能靠 `Set-Cookie` 注入，必须复用真实浏览器 Profile（localStorage 无法跨 profile 注入）。复用方式见附录 B。

---

## 3. 「响应快」是怎么做到的

### 3.1 传输层：把 2.6 MB 压到 786 KB，再让它几乎不再下载

- 全站 **HTTP/2**，CSS/JS/字体同域多路复用，无队头阻塞；TLS1.3 少一个 RTT。
- 业务域与静态域分离：`chat.deepseek.com`（HTML/API，`no-store`）+ `fe-static.deepseek.com`（静态资源，`openresty` 边缘缓存）。静态域可独立扩容、独立缓存、不受业务 cookie 影响。
- **brotli 压缩**：JS 压缩比约 3.4:1（2.64 MB → 786 KB），CSS 6.2:1。
- **内容哈希文件名 + `max-age=2592000`（30 天）**：文件名变即缓存失效，不需要协商请求。
- 字体只做 **Latin 子集**（28 KB/个，`unicode-range` 严格限定），中文走系统字体 —— 避免了 CJK 字体 5–10 MB 的死重量。

【实证】+【推断：静态域分离是可用性与成本考虑，代码中无法证明动机】

### 3.2 首屏预算：只有 3 个资源在关键路径上

`index.html` 里全部资源就这些【实证】：

```html
<script crossorigin defer integrity="sha384-…" onerror="_sriCk(this)" src="…/default-vendors.a2a242fbd2.js"></script>
<script crossorigin defer integrity="sha384-…" onerror="_sriCk(this)" src="…/main.85762178e1.js"></script>
<link href="…/main.cc59a63cb5.css" rel="stylesheet">
```

三个细节值得抄：

1. **`defer` 而非 `async`**：两个包有序执行，`main` 可以假定 `vendors` 已就绪，省掉运行时依赖检查。
2. **SRI `integrity` + `onerror="_sriCk(this)"`**：脚本加载失败时用内联脚本接管渲染一张错误页，并把 `ds_src / ds_duration / ds_encodedBodySize` 上报。等于给「白屏」装了探针 —— 用户看到的不是白屏，是可解释的失败态。
3. **没有预加载、没有 SSR**：`<div id="root"></div>` 是空的，首屏完全靠 JS 渲染。所以它「快」的起点并不是「HTML 已带内容」，这点常被误判。

### 3.3 请求层：把「用户按下发送」之前的等待提前付掉

这是**响应迅速最被低估的一环**，四处「预热」全部实证：

| 机制 | 代码证据 | 效果 |
| --- | --- | --- |
| **会话预创建** | `preCreateSession`：`getSessionPrefetchEnabled()`（默认 `session_prefetch: true`），调用 `POST /api/v0/chat_session/create`，内存缓存最多 3 个、按 TTL 过期（剩余 ≤10 分钟即作废），发送时 `consume()` 取一个 | 首次发消息不必等「建会话」往返 |
| **PoW 预求解** | `tw.getFrozenFeature("pow_prefetch", false)` → `cq.preparePowByScene("completion_like")`；`new Worker(new URL(n.p+n.u(37627)))` 在 **Web Worker** 里解挑战，含 wasm + js 双实现与 `powSolveChallengeSuccess/Failed` 埋点 | 反爬挑战不占用主线程，也不占用发送时的等待 |
| **SSE 断流自愈** | `getSSEAutoResumeConfig()` 默认 `sse_auto_resume_timeout: 2000`；存在 `POST /api/v0/chat/resume_stream`，并能接收 `resumeMessageGotFullMessage` 整包补发 | 网络抖动时用户感知不到「重新生成」 |
| **TTS 资源预取** | 启动 5 秒后 `setTimeout(()=>{n.e(74268).then(…prefetchDecoderAssets())}, 5000)`，可用 `?tts_cold=1` 关闭 | 首次点朗读不必等 95 KB wasm |

另外，进站后 **5 个 `client/settings` 请求并发**（`main / model / provider / web_upgrade / banner`），实测各 55–104 ms，互不阻塞首屏渲染 —— 配置不进关键路径。

### 3.4 首屏之外的一切都不许挡路

登录页面上挂着 5 个以上的第三方 SDK/风控脚本：微信 `wxLogin.js`、Apple `appleid.auth.js`、火山引擎埋点 `collect-rangers-v5.2.11.js`（75 KB）、数美风控 `smcp.min.js`（78 KB）、设备指纹 `fp.min.js`（103 KB）+ `portal101.cn/deviceprofile` 上报。它们全部是**运行时动态插入、异步执行**，实测 `load` 事件被拖到 3058 ms，但 DCL 只有 529 ms、FCP 1660 ms —— **用户看到界面时，这些 SDK 还在路上**。

首页内联脚本还把埋点做成了**开关式**：读 `localStorage.__ds_remote_feature_store` 的 `volcengine_enabled`，`enabled` 才注入 SDK，`unknown` 则挂进 `__ds_report_settlers` 队列等后续裁决。埋点可以整体关掉，这在弱网设备上是实打实的收益。

---

## 4. 「丝滑」是怎么做到的

### 4.1 自研可变高度虚拟列表（`ds-virtual-list`）

不是 `react-window`，是自研组件【实证：类名、CSS 变量、`data-virtual-list-item-key`、高度前缀和实现】：

```css
.ds-virtual-list{overflow-anchor:none;overflow-y:auto;height:100%;overflow-x:hidden}
.ds-virtual-list-items{position:relative}
.ds-virtual-list-visible-items{transform:translateY(0px);
  --dsl-virtual-list-transform-y:0px; --dsl-virtual-list-ios-compensation-y:0px}
```

关键设计：

- **位移用 `transform: translateY()`**，不用 `top`/`margin` → 只走合成，不触发布局；
- **`overflow-anchor: none`** —— 主动关掉浏览器原生滚动锚定，避免和自家高度补偿打架（原生锚定在流式增长时会和虚拟列表互相拉扯，产生「跳一下」）；
- **可变高度**：`itemMinSize: 60`、`itemEstimatedSize: 140`、`itemResizable: true`，配 `ResizeObserver` 实测高度 + 前缀和数组做 offset 查询；
- **短列表自动关虚拟化**：代码为 `runtimeFlag && items.length < 200 ? virtual = false : virtual = true`，即条目少于 200 时直接全量渲染 —— 避免为几十条消息付虚拟化开销。登录态实测补上了取值：4 条消息的会话里 `.ds-virtual-list` **仍然存在**（DOM 仅挂 4 个子节点），说明本账号下 `runtimeFlag = true`，那套「短列表降级」开关并没有生效；
- **iOS 专供补偿**：`--dsl-virtual-list-ios-compensation-y` + `ds-virtual-list--ios-compensation-enabled`（配 `overscroll-behavior:none`），处理键盘弹起/回弹导致的偏移；
- **sticky 分组标题**在 transform 容器里用 `calc(--group-title-sticky-top - var(--dsl-virtual-list-transform-y) + var(--ios-compensation-y))` 校正 —— 这是踩过坑才会有的写法。

### 4.2 面向「流」的 Markdown 渲染器（全站最关键的 500 行）

**没检出 remark / markdown-it / react-markdown / micromark 的库标识**（关键词 0 命中），配套的是「AST 节点带 `position.start.offset` + 一大组手写正则容错函数」的形态，**属于自研解析器/渲染层**【静态确认，不排除是自研二次封装的第三方 AST 工具】。它解决的是三个真实痛点：

**(a) 增量喂入，不重解析全量缓冲**

```js
// class ST（SSE 解析器）中的增量喂入（方法名为压缩后单字母，此处为伪代码）
feed(e) { let n = e.slice(this.consumed); this.consumed = e.length; this.eventParser.feed(n); }
```

`consumed` 游标只看新到的尾巴。XHR `onDownloadProgress` 里读 `responseText`，所以每个 progress 事件只处理增量。【实证】

**(b) 尾部容错：绝不把「还没写完的语法」送去解析**

`s7(text, streaming)` 在每次渲染前裁剪掉不可解析的尾部【实证】：

```js
s9 = /(\*[ \t]?)+$/                       // 未闭合的强调
s4 = /(^|\n)[ \t]*\|[ \t]*[`*]*$/         // 未成形的表格行
s5 = /(^|\n)[ \t]*```[A-Za-z0-9+-]*$/     // 未闭合的代码围栏
// 另有 oH/oV/oW/oz/oq/oZ 处理未闭合的 \( … \) / $…$ / *…*
```

被裁掉的尾巴不会丢，而是通过 `textPredictionStartOffset` 交给**纯文本通道**渲染（`iF` 组件，`memo` 包裹）。

**这就是「流式输出不抖」的根本原因**：已完成的语法只解析一次，正在到达的字符走最便宜的路径，不会出现「半个表格 → 整段布局跳变 → 又变回段落」的闪烁与重排。

**(c) 渲染粒度到 AST 节点**

Markdown 上下文里同时挂 `markdown`（原文）和 `ast`（带 `position.start/offset` 的语法树），每个节点渲染组件都是 `memo`（`iF`/`iU`/`a7`）。增量到达时，只有尾部节点需要重算，前面几百行是命中 memo 的。

补充：首屏并不因此变重 —— 重型渲染器全部外包给分包（KaTeX 84 KB / Mermaid 237 KB / Prism 按字母段拆 4 份），且公式有 **KaTeX 与 WASM 双渲染器 + 失败回退**（`latexRenderer: ["katex","wasm"]`、`hideFallbackBeforeFirstSuccess`）。

### 4.3 滚动跟随是一台状态机，不是 `scrollIntoView`

代码里有明确的三态【实证】：`onUserScroll` / `requestFollowScroll` / `forceScrollToBottom` + `isFollowing`。

- 用户上滚 → 停止跟随，不再被流式内容「拽下去」；
- 回到底部（`scrollTop + offsetHeight > scrollHeight - 52`）→ 恢复跟随；
- 程序化滚动一律 `behavior: "instant"`（不做 smooth scroll，避免与流式增长抢主线程）；
- 滚轮事件里 `preventDefault()` 后手写 `scrollTop += delta`，并用 **rAF 合帧**，滚动调度再叠一层 `setTimeout` 清理（`e0()` 的 rAF → setTimeout 双相调度）。

侧栏历史列表走 `gte_cursor` / `lte_cursor` **游标分页**（`/api/v0/chat_session/fetch_page`），不是全量拉取。

### 4.4 渲染隔离与「自己盯着自己」

- 状态层：**Zustand + Immer**，读取一律走 selector（`useStore(sel)`），不是整树订阅；
- 虚拟列表外层的 transform 更新放在 `useLayoutEffect`，**paint 前完成**，不会出现一帧错位；
- 强制同步渲染（`flushSync`）只用在两处：路由跳转、虚拟列表滚动补偿 —— 没有滥用；
- **自带性能看门狗**：`renderThrashing` 事件会在「1 秒内强制渲染 N 次」时上报 `virtualListRenderAnomaly`；组件不显示、DOM 未挂载也都有专门埋点（`wipAssistantMessageDomMissing`）。这说明他们**把卡顿当线上指标在追**，而不是靠用户反馈。

一个重要反证：`useDeferredValue` / `startTransition` / `useTransition` 在业务包中 **0 次命中**（只在 React 自身代码里）。**它没有用 React 18 并发特性来救场** —— 流畅来自架构，不来自某个 API。

### 4.5 动画与字体：克制

CSS 全量统计【实证】（298 KB）：

| `transition` 目标属性 | 次数 |
| --- | --- |
| `opacity` | 31 |
| `background-color` | 19 |
| `box-shadow` | 11 |
| `color` | 8 |
| `transform` | 3 |
| `all` | 3 |

几乎全是**低成本的合成/重绘属性**；`will-change` 全站只用了 6 处（不是每个动画都贴）；31 个 `@keyframes`；`prefers-reduced-motion: reduce` 有多处显式降级；`contain: strict` 只出现在一个测量用隐藏盒子上。

字体：8 个 `@font-face`（Inter 400/500/600/700 × 正常/斜体），**全部 `font-display: swap` + Latin `unicode-range` 子集**。中文不加载 webfont，直接命中系统字体 —— 这是国内产品最省事也最容易被忽略的一条。

### 4.6 数据层：先给旧数据，再悄悄对齐

- 打开历史会话时优先读 **IndexedDB**（store：`history-message`），再向后端 `/api/v0/chat/history_messages` 校验；
- 后端返回 `cache_control: "MERGE" | "REPLACE"` + `cache_version` / `cache_reset_at`，前端据此增量合并或整体替换（按 `message_id` 去重合并）；
- **IDB 操作全部包 3000 ms 超时**；连续 3 次超时后直接停用 IDB（`idbMaxTimeoutTimes` → 忽略后续读写）。这是「宁可不用缓存，也不能让 IndexedDB 卡住 UI」的取舍 —— 说明他们确实被 IDB 坑过。

【实证，具体合并策略细节未在登录态验证】

---

## 5. 代价与反证：它并不是「零成本流畅」

诚实记录几条反面证据，避免把结论说成神话：

1. **首屏 JS 仍不轻**：解压 2.64 MB 要解析执行，实测登录页有 **2 条 long task：147 ms、304 ms**；登录态复访仍有 **3 条：114 / 81 / 57 ms**。冷启动的「第一下卡顿」是真实存在的，只是被骨架/淡入遮住了。
2. **打开历史会话有一次性卡顿**：登录态实测点开会话触发 **186 ms 长任务**（@5.9 s），期间 LCP 从 492 ms 被刷新到 6192 ms。虚拟列表能保证「滚动不卡」，但保证不了「首次渲染一整屏消息不卡」—— 这一段仍然要一次性解析 Markdown + 布局。
3. **第三方 SDK 拖尾明显**：`load` 3058 ms，而 DCL 只要 529 ms。`fp.min.js` 103 KB、`collect-rangers` 75 KB、`smcp.min.js` 78 KB 都是首次登录页的净重。
4. **埋点请求量超过业务请求**：登录态一次导航里 `gator.volces.com/list` 发了 **20 次**，比 17 个业务 API 还多；虽然异步不阻塞渲染，但吃连接与带宽，弱网下会和业务请求抢队头。
5. **CDN 命中假设极强**：`main.js` 首次拉取实测到过 21 s（弱网/跨境抖动），说明「快」高度依赖边缘命中；源站回源时体验会明显下降。
6. **功能全靠开关**：`session_prefetch`、`pow_prefetch`、`sse_auto_resume_timeout`、`enable_*` 全是 frozen feature flag，灰度期间不同用户体验不同 —— 你在自己账号上看到的「快」，可能是某个 AB 组的结果。
7. **虚拟化阈值是拍出来的**：`< 200 条关闭虚拟化` 意味着 200 条左右的长会话会经历一次渲染策略切换，这条边界值本身就是权衡痕迹。
8. **远程特性开关服务在本机不可达**：`hif-dliq.deepseek.com/query` 4 次 `ERR_CONNECTION_CLOSED`，应用靠静默降级继续跑 —— 说明关键路径没有硬依赖它，但也意味着「本地看到的 UI 状态」可能与灰度配置不一致。

---

## 6. 如果要复刻：按收益/成本排序的落地清单

| 优先级 | 动作 | 收益 | 成本 | 本项目落点 |
| --- | --- | --- | --- | --- |
| P0 | 静态资源内容哈希 + 长缓存 + brotli，静态域与 API 域分离 | 复访网络从 MB 级降到 10 KB 级 | 低 | 构建产物已有 hash，补 CDN 与 `Cache-Control` |
| P0 | 流式 Markdown「尾部容错 + 增量喂入」 | 直接消除流式抖动，是「顺」的最大来源 | 中（需自研/改造解析器） | 见既有 `deepseek-protocol-and-tokens.md` 的消息片段模型 |
| P0 | 消息列表可变高度虚拟化 + `transform` 位移 + `overflow-anchor:none` | 长会话恒定 DOM 量 | 中高 | 先做高度测量与 key 稳定性，再上虚拟化 |
| P1 | 滚动三态状态机（跟随/取消/强制到底，`behavior: instant`） | 用户读上文不再被打断 | 低 | 已有 `scrollToBottomTrigger` 语义 |
| P1 | 会话预创建 + PoW/鉴权预热 | 首字延迟少一次往返 | 中（需后端配合） | 需自定义后端支持 |
| P1 | IndexedDB 历史缓存 + 超时熔断 | 打开旧会话秒开 | 中 | 可直接抄 3 s 超时 + 3 次停用策略 |
| P2 | 重组件分包（KaTeX / Mermaid / 高亮语言包 / wasm） | 首屏 −400 KB 以上 | 低 | 动态 `import()` + 按语言字母段拆 |
| P2 | 字体子集 + `font-display: swap` + 系统中文 | 省 5–10 MB，且不闪 FOIT | 低 | 已有 woff2 资源 |
| P2 | 首屏外 SDK 延迟注入 + 埋点开关 | DCL 提前 1 s 以上 | 低 | — |
| P3 | `renderThrashing` 类自监控 | 把卡顿变成可观测指标 | 低 | 加一条 longtask/强制渲染计数上报 |

**不建议照抄的部分**：SRI + 自定义错误页（有构建链路成本，除非你在意脚本被篡改/加载失败的白屏体验）；iOS scroll 补偿（除非确有 iOS WebView 需求）；606 个分包的粒度（过度拆分会让 chunk 请求数暴涨，需按实际体积分布决定）。

---

## 7. 一句话回答用户的原始问题

> **快**，是因为它在用户还没点发送的时候就把会话、挑战、解码器都准备好了，而且复访时几乎不下载任何东西；
> **顺**，是因为它把「流式文本」当成一个有语法状态的数据流来渲染（尾部容错 + 增量解析 + 节点级 memo），把「长对话」当成一个只有视口内元素存在的列表来渲染（自研虚拟列表 + transform 位移 + 关掉原生滚动锚定），并且所有滚动/动画都收敛到 rAF、`instant`、`opacity/color` 这类不触发重排的路径上。

---

## 附录 A：证据文件索引

| 文件 | 内容 |
| --- | --- |
| `.workbuddy/report-deepseek/runtime2.json` | CDP 首访完整采集（导航、资源、longtask、DOM、CDP metrics） |
| `.workbuddy/report-deepseek/cache_test.json` | 冷/热两次导航的逐资源 `transferSize` 对比 |
| `.workbuddy/report-deepseek/api_probe.txt` | API 与懒加载分包体积/TTFB |
| `.workbuddy/report-deepseek/chunk_table.tsv` | 606 个 chunk 的 id / 名称 / 哈希 |
| `.workbuddy/report-deepseek/probe_log.txt` | 首页响应头（含 `x-ds-served-by`、WAF cookie、CSP、HSTS） |
| `.workbuddy/report-deepseek/ctx1..8.txt` | SSE 解析器、虚拟列表、Markdown 容错、预取逻辑的代码原文切片 |
| `.workbuddy/report-deepseek/css_report.txt` / `css_anim.txt` | 字体、虚拟列表 CSS、transition 属性分布 |
| `.workbuddy/report-deepseek/runtime_auth.json` | **登录态**完整采集（含点开会话前后两段滚动采样） |
| `.workbuddy/report-deepseek/auth_summary.txt` | 登录态实测的可读摘要（导航、longtask、滚动、资源时间线） |
| `.workbuddy/report-deepseek/auth_state.json` | 登录态快照（用户 id、账号掩码、cookie 名，**不含任何 token/cookie 值**） |

## 附录 B：复现命令

```bash
# 抓产物与响应头
node .workbuddy/report-deepseek/probe.cjs
node .workbuddy/report-deepseek/fetch_assets.cjs

# 浏览器实测（需本机 Chrome）
node .workbuddy/report-deepseek/cdp_measure2.cjs      # 未登录登录页
node .workbuddy/report-deepseek/cdp_cache.cjs         # 冷热两次导航

# 登录态实测（首次会开窗口让你登录一次，之后长期复用）
node .workbuddy/report-deepseek/auth_measure.cjs --login-only
node .workbuddy/report-deepseek/auth_measure.cjs --open-first-chat
```

> 登录态实测的原理：脚本用 `--user-data-dir=.workbuddy/chrome-profile` 起一个独立 Chrome（默认端口 9333），你在这个窗口里登录一次，cookie + localStorage 全部落盘；后续每次运行直接 `attach` 到这个 profile 再开新标签页导航。**不碰你日常 Chrome 的配置**，也绕开了 Chrome 136+ 「默认 profile 禁止开远程调试端口」的限制。
>
> 注意：本仓库 `package.json` 含 `"type": "module"`，上述脚本须以 `.cjs` 后缀运行（`probe.cjs` 即为此副本）。
