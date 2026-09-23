# TODO / 待办与已知问题

> 记录时间：2026-09-16
> 最近提交：`4e0e64f 2026-09-14 webpack 画布`
> 来源：一次「用 Mock 模型跑通测试 AI 短剧」的排查过程。当时链路已跑通（3 分镜 / 3 图 / 3 视频），
> 下面记录已修内容与遗留事项，避免后续（尤其是切换真实模型时）遗忘。

---

## 〇、当前进度（2026-09-18 更新）

**已完成：**
- ✅ Mock 链路验收通过（3 分镜 / 3 图 / 3 视频）
- ✅ **P0-1（1c：nginx 本地伺服 + Range）已做完并实测** —— 见下方「P0-1 完成记录」
- ✅ 真实 provider 已建好 3 个（火山引擎 Ark，key 已存进 DB）：
  | 类型 | model_name | executor |
  |---|---|---|
  | `llm` | `doubao-seed-2-1-pro-260915` | `OpenAIClient` → `/chat/completions` |
  | `text2image` | `doubao-seedream-5-0-pro-260628` | `OpenAIImagesGenerationExecutor` → `/images/generations` |
  | `image2video` | `doubao-seedance-1-0-pro-250528` | `VolcengineImage2VideoClient` → `/contents/generations/tasks` |
- ✅ 3 个 Mock provider 已**停用**（`is_active=false`，未删除，可随时切回）。
  实测 `_pick_provider()` 已正确选中三个火山模型。

**下一步：**
1. **新建一集**（别在现有那集上跑 —— 它 8 个阶段全是 `completed`，
   `run_full_pipeline_task` 会当已完成跳过），走完整真实模型链路

2. ⚠️ **必须把提示词模板绑定的模型改成真实 provider，否则跑起来还是 Mock！**

   > 这里曾经写错过一次，记录教训：`run_pipeline` 走的是 `apps/content/processors/` 里的
   > `StageProcessor`，它**直接使用 `PromptTemplate.model_provider`，完全不检查 `is_active`**
   > （`llm_stage.py:471-479`、`text2image_stage.py:414-420`、`image2video_stage.py:374-375`）。
   > 所以「停用 Mock provider」对这条路径**毫无作用**。
   > 而 `_pick_provider()`（`apps/ai_proxy/views.py:118`）那套「按 is_active + priority 取第一个」
   > 的逻辑只适用于**画布的节点执行器**路径 —— 不要把两者混为一谈。

   已于 2026-09-18 改绑完成（默认提示词模板集）：
   | stage_type | 绑定模型 |
   |---|---|
   | `storyboard` | 火山引擎 / `doubao-seed-2-1-pro-260915` |
   | `camera_movement` | 火山引擎 / `doubao-seed-2-1-pro-260915` |
   | `image_generation` | 火山引擎 / `doubao-seedream-5-0-pro-260628` |
   | `video_generation` | 火山引擎 / `doubao-seedance-1-5-pro-251215` |

   改绑命令（按模板现有 provider 的 `provider_type` 找同类型的激活真实 provider）：
   ```python
   for t in PromptTemplate.objects.filter(template_set=ts).select_related("model_provider"):
       new = (ModelProvider.objects
              .filter(provider_type=t.model_provider.provider_type, is_active=True)
              .exclude(executor_class__icontains="mock")
              .order_by("-priority", "-created_at").first())
       t.model_provider = new
       t.save(update_fields=["model_provider", "updated_at"])
   ```

3. （可选）模板**正文**仍是宝宝/皮克斯风格，想去掉可以改 `/admin/prompts` 里的模板内容
4. ✅ 图生视频已换成 `doubao-seedance-1-5-pro-251215`（带音画同步）—— 依赖的 P1-3 audio 误杀 bug 已修

### ⚠️ 2026-09-18 踩坑：模型未在火山控制台「开通」

改完模板绑定后跑第 3 集，storyboard 阶段失败：

```
任务执行失败: 网络请求错误:
('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))
```

**这个报错极具误导性** —— 看起来像网络/防火墙问题，实际根因是模型未开通。
直接对 `chat/completions` 发**非流式**极小请求才拿到真正的原因：

```json
HTTP 404
{"error":{"code":"ModelNotOpen",
 "message":"Your account 2117348197 has not activated the model
            doubao-seed-2-1-pro-260915. Please activate the model service
            in the Ark Console."}}
```

**为什么报错不一样**：`llm_stage.py:180` 走的是**流式**（`ai_client.generate_stream()`，
`openai_client.py:136` 用 `stream=True`）。Ark 对未开通的模型在流式请求下**直接掐断 TCP**，
不返回 JSON 错误体，于是 `requests` 抛 `RemoteDisconnected`，把真实原因吞掉了。

**排查经验（重要）**：
1. 遇到 `RemoteDisconnected` / `Connection aborted` 时，**先用非流式极小请求复现一次**，
   大概率能拿到真正的错误 JSON（ModelNotOpen / InvalidParameter / AuthenticationError …）。
2. **`GET /api/v3/models` 返回的是「平台可用模型清单」，不等于「本账号已开通的模型」。**
   实测该接口返回 133 个，其中绝大多数本账号并未开通 —— 批量导入时**只能勾已经开通的**，
   否则建出来的 provider 一调就废。
3. 开通入口：火山方舟控制台 `https://console.volcengine.com/ark` → 模型开通管理。

### 零成本探测「模型是否已开通」的手法

故意发**省略必填参数**的请求：Ark 会**先解析模型、再校验参数**，于是

| 返回 | 含义 |
|---|---|
| `ModelNotOpen` | **ID 有效，但账号未开通** → 去控制台开通即可用 |
| `MissingParameter` / `InvalidParameter` | **已开通**（已过权限校验，只是参数不对）→ 可直接用 |
| `InvalidEndpointOrModel.NotFound` | **该 ID 对本账号不可用**（不存在 / 无权访问）→ 换 ID 或查接入方式 |

实测（2026-09-18，账号 2117348197）：

```
llm  doubao-seed-2-1-pro-260915        -> HTTP 200                  ✅ 可用
t2i  doubao-seedream-5-0-260128        -> InvalidParameter(size)    ✅ 已开通
t2i  doubao-seedream-5-0-pro-260628    -> ModelNotOpen              ❌ 未开通
i2v  doubao-seedance-1-0-pro-250528    -> ModelNotOpen              ❌ 未开通（可开通）
i2v  doubao-seedance-2-0-260128        -> ModelNotOpen              ❌ 未开通（可开通）
i2v  doubao-seedance-1-5-pro-251215    -> InvalidEndpointOrModel.NotFound  ⚠️ 不可用
i2v  doubao-seedance-1-0-lite-i2v-250428 -> InvalidEndpointOrModel.NotFound ⚠️ 不可用
```

> ⚠️ **控制台的显示名 ≠ API 的 model id**。控制台写「Doubao-Seedream-5.0-lite 已开通」，
> 对应的 API id 是 `doubao-seedream-5-0-260128`（不是 `-pro-260628`）。
> 这就是「明明开通了却用不了」的常见原因。另外「Doubao-Seedance-1.5-pro 已开通」
> 在 API 侧却报 NotFound，怀疑需要走推理接入点（`ep-xxxx`）或另有版本号，待与火山确认。

**已处理**：`image_generation` 模板已改绑到 `doubao-seedream-5-0-260128`，
未开通的 `doubao-seedream-5-0-pro-260628` 已停用。

**待办**：在控制台开通一个返回 `ModelNotOpen` 的图生视频模型
（如 `Doubao-Seedance-2.0` → API id `doubao-seedance-2-0-260128`，
或 `doubao-seedance-1-0-pro-250528`），然后新建 provider 并改绑 `video_generation` 模板。

#### 火山 Seedance 各型号可用性（2026-09-18 实测，账号 2117348197）

探测方式：`POST /api/v3/contents/generations/tasks`，body 省略必填参数 ——
`ModelNotOpen` = ID 有效但未开通；`InvalidEndpointOrModel.NotFound` = 该 ID 对本账号不可用。
每个 ID 最多重试 5 次以排除网络抖动造成的假阴性。

| model id | 结果 | 说明 |
|---|---|---|
| **`doubao-seedance-1-0-pro-250528`** | `ModelNotOpen` | ✅ **可开通，且不在 200 元门槛清单里** |
| **`doubao-seedance-1-0-pro-fast-251015`** | `ModelNotOpen` | ✅ 同上 |
| `doubao-seedance-2-0-260128` | `ModelNotOpen` | 💰 需 200 元门槛 |
| `doubao-seedance-2-0-fast-260128` | `ModelNotOpen` | 💰 需 200 元门槛 |
| `doubao-seedance-2-0-mini-260615` | `ModelNotOpen` | 💰 需 200 元门槛 |
| `doubao-seedance-2-5-260628` | `ModelNotOpen` | 💰 需 200 元门槛 |
| `doubao-seedance-1-5-pro-251215` | `NotFound` | ⚠️ 控制台显示"已开通"，但 API 拒绝 |
| `doubao-seedance-1-0-lite-i2v-250428` | `NotFound` | ❌ 已下线 |
| `doubao-seedance-1-0-lite-t2v-250428` | `NotFound` | ❌ 已下线 |

**「控制台显示已开通、API 却 NotFound」的矛盾（1.5-pro）** —— 已排查完，结论是火山侧不一致：

1. `/api/v3/models` 里**确实有** `doubao-seedance-1-5-pro-251215`（`version: 251215`）
2. 但视频任务接口返回 `404 InvalidEndpointOrModel.NotFound`
3. `/models` 响应里**没有任何"是否已开通"的字段** —— dump 原始 JSON 对比，
   已开通可用的 `doubao-seedream-5-0-260128` 与调不通的 `1-5-pro` 结构完全一致
4. 没有"列出已开通模型"的接口（`/api/v3/endpoints` → 404）
5. 已穷举：5 种 ID 写法（带/不带版本号、加 `-i2v`/`-t2v` 后缀）× 3 类 endpoint 路径，
   全部 `NotFound`；同时对照组 `1-0-pro`/`2-0` 返回的是清晰的 `ModelNotOpen`
   → 说明不是探测方法的问题

**✅ 官方回复（2026-09-18 23:30，火山技术支持工单）—— 疑团已解开**

> 1. **该模型已列入下线计划，模型服务正式下线时间为 2026 年 9 月 21 日 14:00（UTC+8）**，
>    目前处于**下线前停止服务阶段**，因此调用返回 404 `InvalidEndpointOrModel.NotFound`。
>    **您使用的 model 参数 `doubao-seedance-1-5-pro-251215` 写法是正确的**，但模型已无法继续使用，
>    建议尽快迁移至其他视频生成模型，如 `doubao-seedance-2-0-mini-260615`。
> 2. 视频生成模型通过 `/api/v3/contents/generations/tasks` 调用时
>    **可直接使用 Model ID，无需先创建推理接入点**；且该模型已下线，创建接入点也无法恢复。
> 3. 控制台列表展示问题：已收到建议，会同步给相关团队评估。
> 4. **Seedance 系列模型开通条件**（满足任一即可）：
>    - 账户可用余额 ≥ 200 元
>    - 购买 200 元及以上专属节省计划
>    - 已购买对应模型资源包且有余量
>
>    满足条件后前往控制台 → 开通管理开通对应模型，无需额外条件。
>    建议优先迁移至 Seedance 2.0 mini / 2.5 等当前稳定服务的模型。

**结论与教训**

- 诊断链路是对的：`NotFound` 确实意味着「ID 不可用」，而 `ModelNotOpen` 才是「未开通」。
  但当时**误以为 1.5-pro 是"已开通却没权限"**，实际是**模型正在下线** —— 控制台列表未同步，
  所以显示"已开通"。**控制台的"已开通"不等于模型可用。**
- ⚠️ **纠正一处错误推断**：曾根据付费对话框标题（只列了 2.5 / 2.0-mini / 2.0-fast / 2.0）
  推断 `1.0-pro` 可能免费。**错误** —— 官方明确 **Seedance 全系列**都是 200 元门槛。
  不要用对话框里的清单去推断哪些模型免费。
- 曾怀疑需要「推理接入点（ep-xxxx）」，**已排除**：直接用 Model ID 即可。

**当时的处置**：把 `video_generation` 模板 `is_active=False` 先跳过该阶段
（`get_stage_template_states()` 只统计 `is_active=True` 的模板），
以便先验收 分镜 → 文生图 → 运镜。
（后来已改回 `is_active=True` 并改绑到 `doubao-seedance-1-0-pro-250528`。）

**待决策**：视频要走哪条路 —— 充 200 元开 Seedance 2.0 mini/2.5，
换聚合网关（5gtoken 等，按量付费无门槛），本地 ComfyUI，或暂时不要视频。

**待办**：在控制台开通以下三个模型后重跑（`storyboard` 是 `failed` 状态，
`run_full_pipeline_task` 只跳过 `completed` 的阶段，所以**直接再点「运行流程」即可**，不必新建分集）：

| 用途 | 模型 |
|---|---|
| storyboard / camera_movement | `doubao-seed-2-1-pro-260915` |
| image_generation | `doubao-seedream-5-0-pro-260628` |
| video_generation | `doubao-seedance-1-5-pro-251215` |

**环境状态**
4 个容器（backend / celery / frontend / redis）保持运行；数据在
`./backend/data/ai_story.db` 与 `./storage/`，重启不丢。
若已关机，先起 Docker Desktop，再 `docker compose up -d`。

> ⚠️ 那个火山 API Key 曾在对话里明文出现过，**建议跑通后去控制台吊销重建**。

---

## 〇之二、P0-1 完成记录（2026-09-18）

> 注意：原设计**不完整**，实际做了两处 nginx 改动。

**踩到的坑**：原计划只给 `/storage/` 加 alias。但真实 provider 产物落盘后，后端返回的 URL 形状是
**`/api/v1/content/storage/video/xxx.mp4`**（见 `core/ai_client/image2video_client.py:_build_storage_url`、
`core/ai_client/image_result_utils.py:_build_storage_url`），走的是 `/api/` 前缀 ——
那个前缀是转发给 Django 的，**Range 问题依旧**。所以必须两条前缀都接管。

**改动**
```nginx
# docker/nginx/frontend.conf
location /storage/ {                        # legacy 形状
    alias /usr/share/nginx/storage/;
}
location = /api/v1/content/storage/image/ { # 例外：图片列表 JSON 接口留给后端
    proxy_pass http://backend:8010;
    ...
}
location ^~ /api/v1/content/storage/ {      # 真实产物形状
    alias /usr/share/nginx/storage/;
}
```
```yaml
# docker-compose.override.yml
  frontend:
    volumes:
      - ./storage:/usr/share/nginx/storage:ro
```
生效命令：`docker compose up -d --build frontend`（nginx 配置是烘进镜像的）

**实测结果**

| 请求 | 改前 | 改后 |
|---|---|---|
| `/api/v1/content/storage/video/x.mp4` + `Range: bytes=0-1023` | 200 + 整个 6.5MB | **206 + `Content-Range: bytes 0-1023/6497239` + 1024 字节** |
| `/storage/video/x.mp4` + Range | 200 + 整个文件 | **206** |
| 不带 Range（完整下载） | 200 | 200 + `Accept-Ranges: bytes` |
| `/api/v1/content/storage/image/`（列表接口） | 后端 JSON | 仍然后端 JSON（精确匹配生效） |
| 不存在的文件 | — | 404 |

---

## 一、本次已修复（供回溯）

| # | 文件 | 改动 |
|---|------|------|
| 1 | `docker/nginx/frontend.conf` | 新增 `location /storage/`，反代到 `/api/v1/content/storage/` |
| 2 | `backend/config/urls.py` | 新增不受 `DEBUG` 约束的 `re_path(r'^storage/(?P<path>.*)$')` → 302 到 storage API。原因：`static(STORAGE_URL, ...)` 只在 `DEBUG=True` 生效，而容器跑 `production`，导致 MCP 产物 URL（`/storage/...`）全部 404 |
| 3 | `docker-compose.override.yml` | `backend` / `celery` 增加 `env_file: [{path: .env, required: false}]` |
| 4 | `.env`（新建，已被 gitignore） | 随机 `DJANGO_SECRET_KEY`、`MCP_ACCESS_TOKEN`、`AGENT_SERVER_BASE_URL=http://host.docker.internal:9002` |
| 5 | `backend/core/ai_client/mock_llm_client.py` | 修 `_get_mock_response()` 判定顺序。原顺序把「改写」放在「分镜」之前，而分镜模板正文含「不得删减、合并或**改写**原句」，导致分镜请求被误判成 rewrite，返回散文体 → 分镜阶段 `JSON解析失败: Expecting value: line 1 column 1` |
| 6 | `backend/core/ai_client/mock_image2video_client.py` | 替换 `MOCK_VIDEO_URLS`。原 `sample-videos.com`（SSL 证书主机名不匹配）与 `commondatastorage.googleapis.com`（403）均不可用，导致视频阶段 `completed` 但播放器停在 `0:00` |

> ⚠️ 注意：`.env` 里的 `DJANGO_SECRET_KEY` 换成了随机值，**此前签发的 JWT 与 Django session 全部失效，需重新登录**。

---

## 二、遗留事项

### P0 —— 接真实模型之前必须做

#### 1. ✅【已完成 2026-09-18】本地产物用 nginx 直接伺服 + 支持 Range（「1c」）

> 完成记录见顶部「〇之二、P0-1 完成记录」。以下是当初的问题分析，保留作背景。

**问题**
Django 的 `FileResponse` 不支持 HTTP Range 分段请求（`backend/apps/content/views.py:154`），
nginx 目前也只是把 `/storage/` 转发给 Django。实测：

```
GET /storage/video/xxx.mp4   Range: bytes=0-1023
-> HTTP 200            （不是 206）
   Content-Range: (空)
   Content-Length: 6497239   （要 1024 字节，却把整个 6.5MB 全返回）
   Accept-Ranges: (空)
```

**为什么现在没影响到 Mock 环境**
Mock 的图/视频都是**外链**（`picsum.photos`、`test-videos.co.uk`），浏览器直接去外网取，
不经过我们的服务器（实测 `test-videos.co.uk` 完整支持 Range：返回 `206` + `Accept-Ranges: bytes`）。
本地 `storage/` 里目前只有 1 个孤儿文件。

**为什么接真实模型后是刚需**
真实 provider 的产物会落盘到本地 `storage/`，前端拿到的是 `/api/v1/content/storage/video/xxx.mp4`。
没有 Range 时：一个 50MB 的视频浏览器要**整个下完才能播**，进度条拖不动；有 Range 才能秒开、任意拖动。

**改法**
```nginx
# docker/nginx/frontend.conf
location /storage/ {
    alias /usr/share/nginx/storage/;
    add_header Accept-Ranges bytes;
    expires 7d;
    access_log off;
}
```
```yaml
# docker-compose.override.yml
  frontend:
    volumes:
      - ./storage:/usr/share/nginx/storage:ro
```
```powershell
docker compose up -d --build frontend
```
nginx 对静态文件原生支持 Range，无需写代码。

**注意**：这只覆盖 `/storage/` 前缀。前端播放走的是 `/api/v1/content/storage/video/...`，
那条路仍会落到 Django。要一并解决，需要再加 `location ^~ /api/v1/content/storage/` 用 alias，
并且用 `location = /api/v1/content/storage/image/`（精确匹配）把返回 JSON 列表的接口留给后端。

**想现在验证 1c 的话**：把 `MockImage2VideoClient` 改成先把示例视频下载到本地 `storage/` 再返回本地 URL，
就能实测 Range 是否生效。

---

### P1 —— 明确的代码缺陷

#### 2. `execute_video_generation.py` 同步调用 async 漏 `await`

**位置**：`backend/apps/workflows/node_executors/execute_video_generation.py:52`

```python
def execute_video_generation(...):              # 同步函数
    ...
    raw_result = client._generate_video(...)    # _generate_video 是 async def，没有 await
    result = normalize_video_result(raw_result)
```

`_generate_video` 返回 **coroutine 对象**，而 `normalize_video_result`
（`apps/workflows/node_executors/response_helpers.py:77`）只认 `AIResponse` 和 `dict`，
会落到最后的兜底分支返回 `{'success': False, 'error': '无法识别的视频响应格式'}`，
随后第 75 行 `raise RuntimeError`。

**影响范围**：**画布节点执行器**这条路，图生视频必定失败。
`run_pipeline` 走的是 `StageProcessor`（`apps/projects/tasks.py:1242`），**不受影响**。

**为什么测试没抓到**：测试用 `unittest.mock` 的 `return_value` 直接返回 dict，绕过了 await。

**修复方向**：把 `execute_video_generation` 改为 async，或对客户端调用包一层同步桥接
（项目里管道的其余部分都在用 `async_to_sync` / `asyncio.run` 这类模式，需与调用方对齐）。

#### 3. ✅【已修复 2026-09-18】厂商模型发现把「支持音频的视频模型」误杀

**修复记录（结论在前，下面是当初的分析）**

改成**只按 `domain` + 模型名称 + 「输出是否纯音频」**三条规则判定，
**彻底不再扫描 `task_type` / `modalities` / `features`** —— 那三个是多值列表，
模型常同时声明多项能力，用关键词扫必然误杀。

```python
EXCLUDED_MODEL_TOKENS   = [... 含 'audio' ...]   # 只用于匹配模型「名称」
EXCLUDED_MODEL_DOMAINS  = [                       # 只用于匹配「domain」
    'embedding', 'audiogeneration', 'audiotranscription', 'audiounderstanding',
    'speechsynthesis', 'speechrecognition', 'voicesynthesis', 'moderation',
]
```

`_should_exclude_model()` 现在是：① domain 命中 → 排除；② `_has_audio_only_output()`
（只看 `output_modalities`，纯音频才算）→ 排除；③ 名称命中 → 排除。

**顺带修掉了同一类的第二个误杀**：`doubao-seed-2-0-mini-260428` / `-lite-260428`
是正经 VLM，只因为 `task_type` 里顺带有 `SpeechToText`，被 `'speech'` 干掉。

**实测效果（火山引擎）**

| | 修复前 | 修复后 |
|---|---|---|
| 发现模型数 | 118 / 133 | **125 / 133** |
| 仍被排除的 8 个 | 15 个（含 7 个视频模型） | 全是 `doubao-embedding-*`（确实该排除） |
| 「图生视频」可选项 | 7 | **12**（`seedance-1-5-pro`、`seedance-2-0/2-5` 全部回归） |

**回归验证**：13 条合成用例全过（TTS / ASR / embedding / rerank 仍被正确排除）；
`test_vendor_batch` 里 4 个发现相关测试全过（`Ran 4 tests ... OK`）。
该文件另有 3 个 `302ai` 相关失败，是既有问题（`vendor_catalog.py` 里没有 `302ai`），与本次无关。

**改动文件**：`backend/apps/models/services.py`

---

**以下为当初的问题分析（保留作背景）**

**位置**：`backend/apps/models/services.py:74`（`EXCLUDED_MODEL_TOKENS`）+ `:295`（`_should_exclude_model`）

排除词表里含 `'audio'`（本意是过滤 TTS / ASR 语音模型），但 `_should_exclude_model`
把它拿去匹配模型的 `task_type` 与 `modalities` 字段：

```python
for field in ('domain', 'task_type', 'modalities', 'features'):
    metadata_values.extend(_iter_metadata_values(item.get(field)))
if any(token in normalized for normalized in normalized_metadata for token in EXCLUDED_MODEL_TOKENS):
    return True          # ← 'audio' 命中即丢弃
```

**后果**：凡是「能生成/接收音频」的**视频**模型都被当成音频模型丢掉。实测火山引擎：

| 模型 | task_type / modalities | 结果 |
|---|---|---|
| `doubao-seedance-1-0-pro-250528` | `ImageToVideo, TextToVideo` | ✅ 保留 |
| `doubao-seedance-1-5-pro-251215` | `TextToAudioVideo`, `ImageToAudioVideo`, … | ❌ 被误杀 |
| `doubao-seedance-2-0-260128` | `input_modalities` 含 `audio` | ❌ 被误杀 |
| `doubao-seedance-2-5-260628` | 同上 | ❌ 被误杀 |

原始 `/api/v3/models` 返回 **133** 个模型，发现接口只返回 **118** 个，**差的 15 个全被静默过滤**。

**影响**：批量导入 UI 里选不到 seedance 1.5 / 2.x；而项目自己的单元测试
（`apps/models/tests/test_image2video_client.py:232`）恰恰断言的是
`doubao-seedance-1-5-pro-251215` —— 也就是「测试验证过的模型，UI 里反而建不出来」。
只能走「自定义厂商」模式或直接调 API 绕开。

**修复方向**：
- `'audio'` 只应参与 `domain` 判断（如 `domain == 'AudioGeneration'`），
  不应拿 `task_type` / `modalities` 里的 `audio` 作为排除依据；或
- 改成白名单式：仅当 `output_modalities` 完全不含 `video`/`image` 时才排除。

---

#### 4. backend 与 celery 的代码来源不一致

```yaml
# 现状
backend: 只挂 ./backend/data 和 ./storage   -> 跑【镜像里烘进去的代码】
celery : 挂了 ./backend                     -> 跑【宿主机当前代码】
```
后果：**改了后端代码，celery 立刻生效，backend 还是旧的**，两边行为分叉（本次修 Mock 时就需要
`restart celery` + `--build backend` 两条路一起做）。

**修复方向**：给 `backend` 也加 `- ./backend:/app/backend`（本地开发模式），
或统一都不挂、每次改代码都 `--build`。二者选一，保持一致。

---

#### 5. 「停用」模型对 `run_pipeline` 无效：StageProcessor 不检查 `is_active`

**位置**：
- `apps/content/processors/llm_stage.py:471-479`
- `apps/content/processors/text2image_stage.py:414-420`
- `apps/content/processors/image2video_stage.py:374-375`

**现状**：这几个处理器直接采用提示词模板上钉死的 provider，**不判断 `is_active`**：

```python
# llm_stage.py
if not provider:
    provider = template.model_provider        # ← 不查 is_active
if not provider:
    provider = self._get_default_provider()   # ← 只有这条兜底才查 is_active

# text2image_stage.py
if template and template.model_provider:
    return template.model_provider            # ← 直接 return，连兜底都没有
return None
```

**踩坑实录（2026-09-18）**：在 `/admin/models` 把 3 个 Mock provider 停用后，
新建分集跑 `运行流程`，产物**仍然是 Mock 生成的**（`storyboards.model_provider` 外键指向
`Mock LLM API`）。原因是 4 个提示词模板的 `model_provider` 还钉在 Mock 上，而这条路径不看
`is_active`。当时误以为「停用即可」，白跑了一轮。

**影响**：UI 上的「停用」对主流程没有约束力 —— 你以为关掉了某个（例如很贵的）模型，
它其实还在被使用。反过来，删除被模板引用的 provider 也会直接让阶段失败。

**修复方向**（二选一，需产品决策）：
- 模板绑定的 provider 若 `is_active=False`，回落到同类型的激活 provider；
- 或者干脆**报错**并明确提示「模板 X 绑定的模型已停用」，避免静默用错模型。

**临时绕过**：直接改模板绑定（见「〇、当前进度」第 2 条的改绑脚本）。

---

#### 6. ✅【已修复 2026-09-18】上游 LLM 网关连接不稳定，约 40% 概率在 ~19.3 秒处被掐断

**现象**：`run_pipeline` 的 `storyboard` 阶段间歇性失败，报

```
任务执行失败: 网络请求错误:
('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))
```

**排查过程（这轮定位花了不少功夫，记录关键手法）**

1. 先用**非流式极小请求**直连 `chat/completions` → HTTP 200，说明**模型可用、鉴权正常**。
2. 再用**与阶段完全一致的 payload**（流式 + `max_tokens=8192` + 真实模板）连跑多次：

   | 次数 | 结果 |
   |---|---|
   | #1 | ✅ 完整流式，240.0s（399 KB） |
   | #2 | ✅ 完整流式，204.9s（445 KB） |
   | #3 | ❌ `ChunkedEncodingError`，传到一半断（已收 113 KB） |
   | #4 | ❌ `SSLError`，连接阶段就失败（0 字节） |

   症状在 `SSLError` / `ChunkedEncodingError` / `RemoteDisconnected` 之间随机出现
   → 高度怀疑是**连接层**而非业务逻辑。
3. **短请求也失败，且失败耗时精确到 19.27 / 19.28 / 19.28 秒**（成功 7 / 失败 3）。
   这种「定时掐断」不是网络抖动。
4. **关键对照：从宿主机（Windows）直连同样失败**（6 成功 / 4 失败，失败同样是 19.23~19.27s）
   → **排除 Docker/WSL2 网络**，问题在到 Ark 的链路上（疑似免费额度/试用层的网关限流：
   超限请求不返回 429，而是挂起到 ~19.3 秒后掐断）。

**结论**：非代码逻辑问题，也非模型/鉴权问题（那类会返回明确的 JSON 错误）。
但**约 40% 的失败率意味着不加重试就没法用**。

**修复**：`core/ai_client/openai_client.py`

- 原 `generate_stream()` 拆成两层：
  - `_generate_stream_once()` —— 原来的单次实现，逻辑未改；
  - `generate_stream()` —— **连接级重试包装**，默认 3 次尝试、退避 3s/6s。
- 只重试**连接层**错误（命中 `RETRYABLE_ERROR_MARKERS`）；
  **确定性错误**（如 `API请求失败: 404 ...` 模型未开通、401 鉴权）**不重试**，直接抛出。
- 额外处理「既没报错也没收到完成标记」的**静默截断**，按可重试错误处理。
- 每次重试都是一次全新的生成；调用方（`llm_stage`）每收到 token 都用 chunk 里
  **累积的 `full_text`**，所以不会拼出重复内容。
- 重试会打 WARNING 日志，便于事后确认是否发生过。

可用 provider 的 `extra_config` 调整：`stream_retry_attempts`（默认 3）、`stream_retry_backoff`（默认 3 秒）。

**验证**：打桩让前 2 次 `requests.post` 抛 `ConnectionError`（用真实的 `RemoteDisconnected` 文案），
第 3 次放行 → 成功拿到回复，`requests.post` 恰好调用 3 次，日志输出两条重试警告。✅

**遗留建议**：这只是「扛过去」，没有解决根因。如果火山侧确认是试用层限流，
可考虑升级配额，或对该 provider 关闭流式（实测非流式单次请求也能成功，但同样会偶发）。

**补充（同日稍后）：重试已扩展到图像/视频路径**

只修 LLM 流式路径是不够的 —— 随后 `image_generation` 阶段又挂了一次，
日志显示同样症状、同样 ~19.3 秒：

```
分镜 3 响应格式错误: error=网络请求错误:
('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))
```

时间戳 `22:25:57.687` 开始 → `22:26:16.946` 失败 = **19.26 秒**，与 LLM 路径完全一致。
（那一轮 `storyboard` 靠新增的重试**成功产出**，3 张图成功 2 张，第 3 张中招。）

因此抽出共享助手 `backend/core/utils/http_retry.py`：

- `post_with_retry()` / `get_with_retry()`
- 语义：**只重试连接层异常**；HTTP 4xx/5xx 直接返回 Response 交给调用方（确定性错误不重试）
- 默认 3 次尝试、退避 3s/6s，失败打 WARNING 日志

已接入的位置：

| 文件 | 用途 |
|---|---|
| `core/ai_client/openai_client.py` | LLM 非流式 `_generate_text`（流式另有独立重试包装） |
| `core/ai_client/executors/openai_images_generation_executor.py` | 文生图（seedream） |
| `core/ai_client/volcengine_image2video_client.py` | 视频任务创建 + 轮询查询 |

**行为验证**（打桩测试）：前 2 次连接失败 → 第 3 次成功，调用次数恰好 3；
HTTP 404 → 只调用 1 次（不重试）；全部失败 → 抛出最后一次异常。

**仍未接入**（失败可优雅降级，暂不影响主流程）：
`image_result_utils.download_image_to_storage`、`image2video_client` 的产物下载 GET。
它们失败时只会在结果里记 `download_error` 并退回远程 URL。

---

### P2 —— Mock 测试环境的质量问题

#### 7. Mock 依赖外网，且 3 个分镜会出同一段视频

- `mock_text2image_client.py` 用 `picsum.photos`，`mock_image2video_client.py` 用 `test-videos.co.uk`，
  都需外网。容器实测可通，但离线环境会挂。
- Mock 用 `hash(prompt) % 3` 选视频源。而 Mock 的运镜响应是**写死的**（3 个分镜的运镜参数完全相同），
  于是 prompt 相同 → 落到同一档 → **3 个分镜播放同一段视频**。
- 改善方向：把哈希依据换成 `image_uri`（每个分镜图片不同），即可让 3 段视频不同。

#### 8. 提示词集缺 `rewrite` / `asset_extraction` 模板

默认提示词模板集只有 4 个模板（`storyboard`、`image_generation`、`camera_movement`、`video_generation`）。
`get_project_stage_order()`（`backend/apps/projects/utils.py:55`）虽然总会把 `rewrite`、`asset_extraction`
排进执行序列，但 `is_stage_template_enabled()` 会判定为未启用 → **这两个阶段固定被 skip**。
想真正跑「文案改写」需要补对应模板。

---

### P3 —— 数据清理

| 项 | 说明 |
|---|---|
| 3 条死链视频记录 | `generated_videos` 里指向 `sample-videos.com` 的旧记录（「重新生成」是**追加**而非覆盖）。前端按 `order_by('-created_at')` 取最新一条（`apps/projects/views.py:1014`），所以显示正常，但残留会误导 |
| 3 个孤儿分集 | `projects` 中 `series_id IS NULL` 的记录（「未命名项目」×2、「第1集」×1），不会出现在任何作品下 |
| 1 个孤儿视频文件 | `storage/video/2026-09-16/210039_00001.mp4`（6.5MB），DB 里无对应 `generated_videos` 记录。疑似早期 backend/celery 使用不同 SQLite 时留下的 |
| `series` 与账号 | 作品按用户隔离（`SeriesViewSet.get_queryset()` 过滤 `user=request.user`）。当前测试作品挂在 `admin` 名下 |

---

### P4 —— 环境与配套设施

| 项 | 现状 / 待办 |
|---|---|
| **Agent Server** | `http://127.0.0.1:9002` 无监听。`.env` 已把 `AGENT_SERVER_BASE_URL` 设为 `http://host.docker.internal:9002`（容器内 `127.0.0.1` 指容器自身）。`AGENT_SERVER_USERNAME` / `AGENT_SERVER_PASSWORD` 在 `.env` 里**保持注释**——因为 `env_file` 里写 `KEY=` 会把 `base.py` 的默认值 `'test'` 覆盖成空串，反而锁死认证。等 Agent Server 起来并对齐凭据后再取消注释 |
| **Redis 未发布到宿主机** | 只有 `6379/tcp`（容器内），容器互访正常，但宿主机 `redis-cli` 连不上，调试不便 |
| **前端无热重载** | Docker 的 frontend 是 **nginx 静态托管**（`docker/frontend.Dockerfile`），不是 dev server。改 Vue 代码必须 `docker compose up -d --build frontend`。本机也没有 `frontend/node_modules` |
| **`uv` 未安装** | `AGENTS.md` / `CLAUDE.md` 里的 `uv run python manage.py test` 在本机跑不了。可用容器替代（见下） |
| **空表** | `workflow_node_schemas=0`、`workflow_definitions=0`、`global_variables=0`（资产）、`screenplays=0`（剧本）。涉及「画布节点结构定义」「剧本管理」「资产绑定」的功能目前是空壳 |

---

## 三、常用运维命令（本次沉淀）

```powershell
# 启动 / 重建
docker compose up -d
docker compose up -d --build backend frontend          # 改了镜像内代码或 nginx 配置
docker compose up -d --force-recreate backend celery   # 只改了 env_file

# 改了后端代码（celery 挂了宿主机目录，backend 没有）
docker compose restart celery
docker compose up -d --build backend

# 在容器里跑 Django 命令（替代 uv）
docker exec -i -w /app/backend ai_story-celery-1 python manage.py check
docker exec -i -w /app/backend ai_story-celery-1 python manage.py test apps.workflows.tests.test_views -v 2

# 容器内跑一段 Django shell 脚本
@'
import os, django
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.production"
django.setup()
# ... 你的代码
'@ | docker exec -i -w /app/backend ai_story-celery-1 python -

# 日志
docker logs ai_story-backend-1 --tail 50
docker logs ai_story-celery-1 --tail 50
docker logs ai_story-frontend-1 --tail 50
```

**关键端口**：前端 `http://localhost:3000`（Vue 管理端在 `/admin/` 子路径，如 `/admin/series`），
后端 `http://localhost:8010`，Redis 仅容器内 `redis:6379`。

**测试流程**：`/admin/series` 建作品 → 作品内建分集（**提示词集是必填**，新作品不会自动带）
→ 分集详情点「运行流程」。

---

## 四、切换到真实模型的检查清单

1. `/models` 里**停用 3 个 Mock provider**（`is_active=false`）。
   `_pick_provider()`（`backend/apps/ai_proxy/views.py:118`）按 `is_active=True` 过滤后
   `order_by('-priority','-created_at')` 取第一个，停用 Mock 后真实 provider 自动接管。
   （兜底做法：把真实 provider 的 `priority` 设成大于 `100`，因为 Mock 现在是 100。）
2. 执行器类对照（`backend/apps/models/models.py:25-49`）：
   - `llm` → `core.ai_client.openai_client.OpenAIClient`
   - `text2image` → `...executors.openai_images_generation_executor.OpenAIImagesGenerationExecutor`
     / `...executors.chat_completions_image_executor.ChatCompletionsImageExecutor` / `ComfyUIClient`
   - `image2video` → `core.ai_client.image2video_client.VideoGeneratorClient`
     / `core.ai_client.volcengine_image2video_client.VolcengineImage2VideoClient` / `ComfyUIClient`
   - `image_edit` → `...executors.openai_images_edit_executor.OpenAIImagesEditExecutor` / `ImageEditClient`
3. **先做第 1 项（nginx 本地伺服 + Range）**，否则本地大视频播不动。
4. 提示词集换成适配真实模型的内容（Mock 那套模板是钉在 Mock 上的）。
5. 建议：**保留 Mock provider 只停用不删除**，需要回归测链路时一键切回，成本为零。
