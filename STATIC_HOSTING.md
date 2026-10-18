# 静态托管可行性评估与配置步骤

> 评估对象：`skyglow/`（开发版）与 `skyglow-release/`（发布版）
> 评估时间：2026-10-18
> 结论：**当前代码不能直接静态托管**。原因是页面只是壳，全部计算在 Python 后端。

---

## 一、结论先说

| 判断 | 说明 |
|---|---|
| 能否把现有目录直接推到 GitHub Pages？ | **不能**。页面能打开，但所有数据请求返回 404，界面是一片空壳。 |
| 技术上是否可行？ | **可行，但需要改写**。核心算法不依赖 numpy/eccodes，只有 `math`/`json`，可以移植到浏览器。 |
| 工作量 | 中等偏大：约 1500–2000 行 Python 需转成 JS。 |
| 是否值得 | 取决于目标：只想给个演示页 → 可行；要完整功能（含全国地图） → 建议用后端托管。 |

---

## 二、为什么不能直接托管（实测证据）

### 1. 全部计算在后端

前端 2111 行 JS 只负责画图与交互，所有数据来自 9 个后端接口：

```
/api/predict    单日预测（核心）
/api/outlook    七日趋势
/api/azimuth    方位雷达 + 几何剖面
/api/glowmap    全国 / 城市辉光地图
/api/geocode    地名解析
/api/validation 回测验证报告
/api/direct     直连设置
/api/weatherapi 校验源状态
/api/qweather   和风天气状态
```

后端共 **3332 行 Python**。GitHub Pages 只提供静态文件，没有进程、没有 Python、不能执行任何服务端代码。

### 2. 静态托管时 `/api/*` 必然 404

`web/` 目录里只有 4 个前端文件 + 地图数据，**不存在 `api` 目录**：

```
web/
├─ analysis.js
├─ app.js
├─ china.json
├─ index.html
├─ maps.js
└─ styles.css
```

`fetch('/api/predict')` 在 `https://<user>.github.io/skyglow/` 上会请求
`https://<user>.github.io/api/predict` → 404。

### 3. 资源用了绝对路径，项目页直接崩

```
web/index.html:9    <link rel="stylesheet" href="/styles.css">
web/index.html:463  <script src="/app.js"></script>
web/index.html:464  <script src="/maps.js"></script>
web/index.html:465  <script src="/analysis.js"></script>
```

绝对路径 `/app.js` 在项目页 `https://<user>.github.io/skyglow/` 下会解析成
`https://<user>.github.io/app.js` → 404。**即使把后端问题解决了，这一项也必须改。**

### 4. 依赖浏览器无法使用的组件

| 模块 | 依赖 | 浏览器可用？ |
|---|---|---|
| `direct.py`（官方档案直连） | `eccodes`（C 库解码 GRIB2） | **不可能** |
| `glowmap.py`（全国 608 格点） | 3 次批量请求 + 服务端缓存 | 可改写，但很重 |
| `store.py`（磁盘缓存） | 文件系统 | 改用 IndexedDB |
| `config.py`（密钥读写） | 文件系统 | **密钥无处可藏** |

### 5. 密钥无法隐藏（最关键的安全问题）

静态站点的 JS 是明文下发的。任何写进前端代码的 API Key，用户按 F12 就能看到。
当前设计把密钥放在 `config.local.json`（服务端），这正是它安全的原因。
一旦静态化，WeatherAPI / 和风天气的密钥**必然泄露**。

---

## 三、好消息：核心算法可以移植

这是评估里最积极的一条。真正决定预测结果的模块，**只依赖 `math` 和 `json`**：

```
core/model.py    -> json, math, os, timedelta     306 行
core/spectrum.py -> math                          179 行
core/solar.py    -> math, datetime                125 行
core/nn.py       -> json, math, os                 50 行
```

没有 numpy、没有 scipy、没有编译扩展。逐行翻译成 JS 是机械工作，数值可做到一致。

### 外部接口也支持跨域

实测 Open-Meteo 返回：

```
access-control-allow-origin: *
access-control-allow-methods: GET, POST, OPTIONS
```

| 接口 | CORS | 说明 |
|---|---|---|
| `api.open-meteo.com` | ✅ `*` | 可直接浏览器调用 |
| `geocoding-api.open-meteo.com` | ✅ `*` | 地名解析可前端做 |

### 多坐标单请求已验证可用

上游 25 格点采样可以合并成一次请求（实测 200 坐标一次返回 200 条）。
浏览器也能用，只要把 URL 长度控制在约 **8 KB** 以内：

| 坐标数 | URL 长度 | 结果 |
|---|---|---|
| 100 | 1,514 | ✅ |
| 200 | 2,914 | ✅ |
| 300 | 4,348 | ✅ |
| 608 | ~8,700 | ❌ HTTP 414 |

所以全国地图（608 格点）需要分 **3 批**请求，可行但不能一次拿完。

---

## 四、三条路线，按投入排序

### 路线 A：完全静态（纯前端重写）

把 4 个核心模块翻成 JS，浏览器直接算。

- **可保留**：单日预测、七日趋势、方位雷达、地名解析、城市地图、回测报告静态页
- **必须砍掉**：官方档案直连（GRIB 解码）、全国地图（太重，或降级为预计算快照）
- **必须改**：所有 `/api/*` → 直连 Open-Meteo；绝对路径 → 相对路径；缓存 → IndexedDB
- **密钥**：WeatherAPI / 和风天气只能让用户在浏览器里自己填，并存 `localStorage`（明文，但至少不是你的密钥）
- **工作量**：约 1500–2000 行 JS 新代码，2–3 天
- **代价**：每用户的请求走他自己的 IP，共享 Open-Meteo 的 10,000 次/天免费额度，重度使用会撞 429

### 路线 B：静态前端 + 无服务器 API（推荐折中）

前端留在 GitHub Pages，后端换成 Cloudflare Workers / Vercel / Deno Deploy。

- 页面仍是静态托管，但要配一个 `API_BASE` 指向你的 Worker
- 计算逻辑用 JS 重写**或**保留 Python（Vercel 支持 Python 函数）
- 密钥放 Worker 的环境变量，不泄露
- **工作量**：1–2 天，只需重写 `service.py` 的编排层
- **代价**：不再"纯"静态；免费额度仍够个人用

### 路线 C：不做静态，直接部署 Python 服务

Render / Railway / Fly.io / 阿里云函数计算，`python server.py` 原样跑。

- **零代码改动**，功能完整
- 保留 eccodes 直连、全国地图、磁盘缓存
- **工作量**：半天，主要是选平台与配环境变量
- **代价**：需要一台能跑 Python 的机器；免费层可能休眠

---

## 五、如果选路线 A，具体步骤

### 第 1 步：准备分支

```bash
cd skyglow
git init
git add .
git commit -m "init"
git branch -M main
```

`.gitignore` 已包含 `config.local.json`、`*.pem`、`data/cache`、`.venv`，密钥不会入库。

### 第 2 步：新建静态目录

```
docs/                    # GitHub Pages 只能发布根目录或 /docs
├─ index.html            # 从 web/index.html 复制
├─ app.js
├─ maps.js
├─ analysis.js
├─ styles.css
├─ china.json
└─ data/
   ├─ model.json         # 从 data/ 复制，预测参数
   ├─ nn_model.json
   └─ validation.json
```

### 第 3 步：改绝对路径

```html
<!-- 改前 -->
<link rel="stylesheet" href="/styles.css">
<script src="/app.js"></script>

<!-- 改后：相对路径，项目页与自定义域都能用 -->
<link rel="stylesheet" href="./styles.css">
<script src="./app.js"></script>
```

或者更省事，在 `<head>` 加一行：

```html
<base href="./">
```

### 第 4 步：把 `/api/predict` 换成浏览器直算

在 `docs/` 下新建 `compute.js`，逐模块翻译：

```
solar.js      <- core/solar.py      125 行
spectrum.js   <- core/spectrum.py   179 行
model.js      <- core/model.py      306 行
nn.js         <- core/nn.py          50 行
meteo.js      <- core/meteo.py 的 fetch_bundle / assemble / _interp / available
```

`meteo.js` 的关键改动：把 `urllib.request` 换成 `fetch`，把磁盘缓存换成 `localStorage`：

```js
const CACHE_TTL = 30 * 60 * 1000;
async function fetchBundle(lat, lon, start, end) {
  const key = `om:${lat.toFixed(2)}:${lon.toFixed(2)}:${start}`;
  const hit = JSON.parse(localStorage.getItem(key) || 'null');
  if (hit && Date.now() - hit.t < CACHE_TTL) return hit.v;

  const pts = samplePoints(lat, lon);          // 25 个格点
  const url = 'https://api.open-meteo.com/v1/forecast'
    + `?latitude=${pts.map(p => p.lat.toFixed(4)).join(',')}`
    + `&longitude=${pts.map(p => p.lon.toFixed(4)).join(',')}`
    + `&hourly=${FORECAST_VARS}&models=${MODELS_PARAM}`
    + `&start_date=${start}&end_date=${end}&timezone=auto`;

  const res = await fetch(url);                // CORS 已确认放行
  if (!res.ok) throw new Error('Open-Meteo ' + res.status);
  const data = await res.json();
  const val = Array.isArray(data) ? data : [data];
  localStorage.setItem(key, JSON.stringify({ t: Date.now(), v: val }));
  return val;
}
```

模型参数直接读同目录的 JSON：

```js
const model = await (await fetch('./data/model.json')).json();
```

### 第 5 步：删掉或降级不支持的模块

| 功能 | 处理 |
|---|---|
| 官方档案直连（ECMWF/GFS/ICON GRIB） | 整个移除，UI 里去掉开关 |
| 全国辉光地图 | 改为预计算快照（每天跑一次 GitHub Actions 生成 `national.json`），或移除 |
| 和风天气 / WeatherAPI 面板 | 改为本地输入 + `localStorage`，并明确提示"密钥存于本机浏览器" |

### 第 6 步：开启 GitHub Pages

**网页操作**：

1. 把仓库推到 GitHub（`main` 分支）
2. 仓库 → **Settings** → 左侧 **Pages**
3. **Source** 选 `Deploy from a branch`
4. **Branch** 选 `main`，目录选 **`/docs`**，点 **Save**
5. 等 1–2 分钟，访问 `https://<用户名>.github.io/<仓库名>/`

**命令行（gh CLI）**：

```bash
gh api -X POST repos/<用户名>/<仓库名>/pages \
  -f 'source[branch]=main' -f 'source[path]=/docs'
```

### 第 7 步：加 .nojekyll

GitHub Pages 默认跑 Jekyll，会忽略下划线开头的文件。放一个空文件避免意外：

```bash
touch docs/.nojekyll
```

### 第 8 步：可选，绑定自定义域名

1. 仓库 → Settings → Pages → **Custom domain** 填 `sky.example.com`
2. 在域名商加 CNAME 记录指向 `<用户名>.github.io`
3. 勾选 **Enforce HTTPS**

---

## 六、如果选路线 C（改动最小），步骤

以 Render 为例：

1. 推仓库到 GitHub
2. Render → **New** → **Web Service** → 连仓库
3. 配置：
   - **Build Command**：`pip install -r requirements.txt`
   - **Start Command**：`python server.py $PORT`
   - **Environment**：加 `WEATHERAPI_KEY`（如需要）
4. **无需改代码**——`server.py` 已支持云平台惯例（本次已改好）：

   | 情况 | 行为 |
   |---|---|
   | 无 `PORT` 环境变量 | 绑定 `127.0.0.1:8770`，只允许本机访问 |
   | 有 `PORT`（Render/Railway/Fly 会自动设） | 绑定 `0.0.0.0:$PORT`，允许外部访问 |
   | 想强制指定 | 设 `SKYGLOW_HOST=0.0.0.0` |

   也可命令行传端口：`python server.py 8080`。

5. 首次部署会自动装 `eccodes`，直连功能可用。

---

## 七、必须提醒的两件事

### 1. 密钥绝不要进前端

如果走路线 A，**不要**把 WeatherAPI 或和风天气的密钥写进 JS。
要么让用户自填（存 `localStorage`），要么走路线 B 把密钥留在服务端。
写进前端等于公开，爬虫几分钟就能扫到。

### 2. 数据量与速率

Open-Meteo 免费额度是 **10,000 次/天/IP**。静态化后每个访客用自己的 IP：
- 单次查询：约 2 次请求（预报 + 空气质量），缓存 30 分钟
- 全国地图：3 次批量请求
- 正常浏览不会超限，但同一个人反复刷新地图要留意

---

## 八、建议

按你的目标选：

| 你的目标 | 建议 |
|---|---|
| 只是想让别人点开就能看 | **路线 C**：Render 免费层部署，半天搞定，功能完整 |
| 想零成本、无服务器 | **路线 B**：Pages + Cloudflare Worker，密钥安全，1–2 天 |
| 想做成纯前端项目、可 fork | **路线 A**：接受砍掉直连与全国地图，2–3 天 |

如果目标是"作品展示"，路线 C 的性价比最高——现有代码一行不改就能跑，
只是需要把 `127.0.0.1` 换成 `0.0.0.0`。
