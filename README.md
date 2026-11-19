# 妆译 / Makeup Translator

把一张“只有成妆、没有教程”的参考图片，转化为结合用户面部资料、熟练度和已有产品的完整复刻教程。

真实教程先分析参考图并检索技法，再由独立后台任务生成复刻步骤和匹配产品。两个结果分别保存、展示和重试；产品稍后完成时按目标与用途关联已有步骤。步骤编排由 LangGraph 完成，产品补全继续使用 Qwen-Agent。推理模型通过 OpenAI 兼容服务配置，教程文本模型可单独用 `TUTORIAL_TEXT_MODEL` 设置。每一步保存参考图依据、用户面部依据、技法来源、产品要求和绘图参数。技术架构与实现见 [Agent 工作流与实现](docs/agent-implementation.md)。

网页基础功能包含：

- 图片输入校验、元数据记录和 Qwen 多模态解析适配器；
- 模型自然语言枚举值归一化，并在结果中保留 `normalization_notes` 审计记录；
- 妆面特征、用户条件、产品、技法卡和教程步骤的严格数据结构；
- 只依据参考妆效的逐目标技法检索，以及独立的产品匹配；
- 每一步配有可缩放的落点与方向 SVG 示意图；图由技法卡绑定并随教程版本保存，简化步骤时同步更新；
- 产品 `draft → pending_user_review → confirmed` 状态机及联网资料补全；
- SQLite 持久化的个人资料、产品、任务与妆容日记，旧产品 JSON 会一次性迁移并保留原文件；
- 产品编辑、删除、撤回确认、批量补全，以及每次最多 3 件产品、1 个教程的任务队列；
- 化妆包可分别上传外包装和打开后的产品照片；包装照用于文字识别，产品照用于定位色块并从原图提取可见颜色；
- 固定的梳妆台、化妆包、妆容日记、我的资料四个入口，以及手机底部导航；
- 确定性教程基线、LangGraph 个性化教程规划与规则审核；

## 目录

```text
makeup_translator/       核心流程、模型和适配器
data/knowledge_cards.json  官网公开教程重建的技法卡；由 scripts/build_sourced_techniques.py 生成
examples/                用户条件与已有产品示例
tests/                   端到端测试
examples/fixture.png     用于离线演示的合成色块图
```

## 安装与离线跑通

推荐 Python 3.11—3.13。

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/makeup-translator \
  examples/fixture.png \
  --provider fixture \
  --output outputs/demo.json
```

`fixture` 仅验证完整数据流，不进行图片语义识别；输出的 `provider` 会明确标记这一点。

## 使用真实模型进行图片解析

```bash
cp .env.example .env
# 将 DASHSCOPE_API_KEY 写入本地 .env，并在 shell 中加载环境变量
set -a; source .env; set +a
.venv/bin/makeup-translator path/to/reference.jpg \
  --provider qwen \
  --output outputs/qwen-demo.json
```

图片会以 Base64 发送到配置的模型服务。不要提交 `.env`、用户原图或包含隐私的输出。

在“我的化妆包”录入时，两张照片均可选：外包装照片帮助读取品牌、名称和色号；打开后的产品照片会启动独立的颜色任务。视觉模型只定位膏体或色盘区域，RGB 由本地 Pillow 从原始照片提取。多色盘会保留多个色块，也可在产品卡片上手动点选区域校正。手动填写的产品信息不会被包装识别覆盖。没有视觉模型密钥时仍可录入产品，并可手动标记色块。

参考妆面分析会提供眼、颊、唇的可见颜色区域，本地从上传的参考原图取色。教程优先按产品类别和具体用途筛选，再参考照片颜色、色彩家族和妆效排序已有产品。界面展示已有产品的颜色接近程度和匹配依据，并将目录候选标为供查找的参考。修改或删除产品照会取消旧颜色任务；旧教程继续保留创建时的产品快照。

真人妆教步骤图还需要 `pip install '.[images]'`，以及在 `.env` 中设置独立的
`IMAGE_API_KEY`、`IMAGE_BASE_URL` 和 `IMAGE_MODEL`。默认图片模型为
`gpt-image-2.5-sunburst`，标准底图通过图片 API 生成。人脸定位使用可选的
[MediaPipe Face Landmarker 模型](https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task)。下载后在本地环境设置 FACE_LANDMARKER_MODEL 为模型绝对路径。上传素颜照后，面部观察与标准底图分别进入后台任务。
步骤图由本地程序根据标准底图、参考图取色和步骤绘图参数绘制，最多四张关键妆效图使用局部合成；其余步骤使用标准局部图或 SVG 示意。
没有可靠参考颜色时只标出操作范围，没有可用底图时继续显示 SVG 示意。

`--provider qwen` 是为兼容已有接口保留的模式名，当前教程由 LangGraph 编排真实模型；底层推理模型不必属于 Qwen 系列。

未传入 `--profile` 时使用通用用户条件；未传入 `--products` 时产品清单为空。程序不再静默加载演示资料。

## 启动网页

首次安装后端与前端依赖并构建网页：

```bash
.venv/bin/pip install -e '.[dev]'
cd web
npm ci
npm run build
cd ..
```

启动完整网站：

```bash
.venv/bin/makeup-web --reload
```

浏览器打开 <http://127.0.0.1:8000>。启动命令会自动读取项目根目录的 `.env`，构建后的 React 页面由 FastAPI 一起提供。

开发前端时可以另开一个终端运行 `cd web && npm run dev`，浏览器打开 <http://127.0.0.1:5173>；Vite 会把 `/api` 请求转发到 8000 端口。

图片教程和产品联网补全都通过独立工作进程执行。网页读取各阶段进度，切换页面不会中断任务。产品核验有 60 秒硬上限；到时根据已保存的来源快照生成未确认的部分结果，点击“继续核验”才启动新的 60 秒任务。教程各分支保留 600 秒运行上限，步骤图有独立预算。这些是后台任务时限，与用户化妆时间无关。时间配置位于 [`data/task_budget.json`](data/task_budget.json)。任务记录保存在 SQLite；重启后未完成任务会标记为中断，且不会自动重新发送付费请求。

本地数据默认保存在 `runtime/products.sqlite3`，教程和用户上传图片保存在 `runtime/jobs/`。删除产品、资料照片或日记时会一并删除相应本地媒体。旧 `runtime/products.json` 只在首次启动时迁移，之后不会改写。

## 产品入库和联网补全

产品只有经过用户确认后才能导出给教程系统：

```bash
# 1. 用户原始输入，只创建 draft
.venv/bin/makeup-products add \
  --brand "品牌" --name "产品名称" \
  --model-or-shade "型号或色号" --market "中国大陆"

# 2. 使用上一步输出的 product id 强制联网检索
set -a; source .env; set +a
.venv/bin/makeup-products enrich PRODUCT_ID

# 3. 查看官网、测评来源、冲突和待确认字段
.venv/bin/makeup-products show PRODUCT_ID

# 4. 用户审查后明确确认
.venv/bin/makeup-products confirm PRODUCT_ID

# 5. 只导出 confirmed 产品
.venv/bin/makeup-products export-confirmed --output runtime/confirmed-products.json
```

之后把确认产品传给教程：

```bash
.venv/bin/makeup-translator reference.jpg --provider qwen \
  --profile my-profile.json \
  --products runtime/confirmed-products.json \
  --output outputs/tutorial.json
```

联网补全通过独立搜索适配器执行：AIHubMix 配置使用 `模型名:surfing`，百炼配置仍可回退到原生联网搜索。已知品牌的返回链接会再次按核验官网域名过滤；未知品牌可以发现候选来源，但不会标记为官网已确认。测评搜索只接受小红书、抖音和 B 站的公开可检索结果，并明确按“搜索摘要”展示。搜索摘要只负责发现候选，官方字段必须随后读取真实页面正文并通过逐字段原文校验。
AIHubMix 路径要求模型返回固定的 URL/标题/摘要 JSON，并丢弃格式错误、非 HTTP 或越出限定域名的链接；百炼回退路径仍以原生 `search_info` 作为来源白名单。两条路径都会实际读取候选页面。缺少可信身份来源时，默认阻止 `confirm`；只有用户已经通过包装等方式自行确认身份时，才能显式使用 `--accept-unverified-sources` 覆盖该限制。

联网调用受 [`data/search_budget.json`](data/search_budget.json) 的 harness 约束：产品身份搜索和测评搜索各最多一次且并发执行，最多读取两个页面。AIHubMix 搜索模型由 `SEARCH_MODEL` 配置并自动加上 `:surfing`；产品核验任务统一在 60 秒内结束，约 52 秒后停止发起新查询。网页在 8 秒后将任务显示为后台处理，模型不能自行增加搜索轮数。

标准产品目录覆盖底妆、眉妆、眼影、眼线、睫毛、修容、高光、腮红和唇妆。采集范围保存在 `data/catalog_scope.json`：每类包含 30 个淘宝旗舰店销量排序/公开榜单目标，并额外保留用户指定品牌。淘宝公开结果受广告和个性化影响，因此保存为“销量排序搜索出现顺序”，同时保留付款人数文本、店铺和商品链接。

目录事实首选品牌官网；官网不可读时，只接受淘宝/天猫旗舰店或丝芙兰商品页。官网结构化导入不会覆盖其他品牌记录：

```bash
PYTHONPATH=. .venv/bin/python scripts/import_shopify_catalog.py
PYTHONPATH=. .venv/bin/python scripts/import_canmake_catalog.py
PYTHONPATH=. .venv/bin/python scripts/import_curated_official_catalog.py
PYTHONPATH=. .venv/bin/python scripts/sync_catalog_scope_status.py
```

`catalog_curated_official.json` 保存人工核对过的官网产品线与完整公开色号，导入时会校验品牌域名并记录页面访问状态。来源记录保留原始官网链接和页面访问状态。

淘宝排名采集首次需要在本机浏览器登录，会话只保存在已忽略的 `runtime/` 目录：

```bash
PYTHONPATH=. .venv/bin/python scripts/collect_taobao_rankings.py --login
PYTHONPATH=. .venv/bin/python scripts/collect_taobao_rankings.py --collect --max-pages 3
PYTHONPATH=. .venv/bin/python scripts/merge_taobao_scope.py
PYTHONPATH=. .venv/bin/python scripts/collect_taobao_products.py --max-pages 3 --max-lines 3
PYTHONPATH=. .venv/bin/python scripts/import_taobao_catalog.py
PYTHONPATH=. .venv/bin/python scripts/sync_catalog_scope_status.py
```

正装采集会进入同一旗舰店的商品详情页，最多保留三条不同产品线，并展开页面当前公开的全部“颜色分类”选项。小样、试用装、尝鲜装和旅行装会被排除；页面没有明确列出的色号不会推测。

## 项目展望

- **妆教交互**：探索逐步跟练、局部步骤调整和教程版本对比，让用户按自己的节奏练习和回看。
- **技法知识库**：扩展不同妆效和面部特征对应的技法卡，完善来源维护，并探索在部位和用途规则的基础上加入语义检索。
- **产品匹配**：细化粉底、散粉等产品的用途标签，引入条件可比的试色素材，完善跨品牌色号匹配。
- **任务恢复**：扩展阶段级检查点和局部重规划，在资料或产品更新时复用可用的中间结果。

## 第三方来源

- Qwen-Agent: <https://github.com/QwenLM/Qwen-Agent>，Apache-2.0。
- LangGraph: <https://github.com/langchain-ai/langgraph>，MIT。
- MediaPipe Face Landmarker：用于本地人脸关键点定位和裁剪，见 [第三方来源记录](THIRD_PARTY.md)。
