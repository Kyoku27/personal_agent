# Web 管理端方案：后端 + 前端

用于管理个人智能体各功能的前端界面，采用「后端 API + 前端 SPA」架构。

---

## 一、整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        前端 (Frontend)                            │
│  React / Vue / Next.js 等 SPA，运行在浏览器                      │
│  端口: 3000 (开发) / 80 (生产)                                   │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP / REST API
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                        后端 (Backend)                             │
│  FastAPI / Flask，运行在 agent 项目内                            │
│  端口: 8000                                                      │
│  职责: 调用 src/features 下的各模块，暴露 REST API               │
└────────────────────────────┬────────────────────────────────────┘
                             │ 直接调用
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    业务逻辑层 (src/features)                      │
│  页面分析、电商、Meta 广告、飞书、市场分析、资料整合              │
└─────────────────────────────────────────────────────────────────┘
```

---

## 二、目录结构（区域划分）

```
c:\Projects\
├── agent/                          # 后端 + 业务逻辑（已有）
│   ├── src/
│   │   ├── api/                    # 【新增】 Web API 层
│   │   │   ├── __init__.py
│   │   │   ├── main.py             # FastAPI 应用入口
│   │   │   ├── routers/
│   │   │   │   ├── page_analysis.py
│   │   │   │   ├── ecommerce.py
│   │   │   │   ├── meta_ads.py
│   │   │   │   ├── feishu.py
│   │   │   │   ├── market_research.py
│   │   │   │   └── data_integration.py
│   │   │   └── schemas/            # Pydantic 请求/响应模型
│   │   │       └── ...
│   │   ├── core/
│   │   ├── config/
│   │   └── features/               # 业务逻辑（已有）
│   ├── requirements.txt
│   └── run_api.py                  # 启动后端: python run_api.py
│
└── agent-web/                      # 【新增】 前端项目（独立目录）
    ├── package.json
    ├── vite.config.ts
    ├── src/
    │   ├── main.tsx
    │   ├── App.tsx
    │   ├── api/                    # 前端 API 调用封装
    │   │   └── client.ts
    │   ├── pages/                  # 页面
    │   │   ├── Dashboard.tsx       # 首页
    │   │   ├── PageAnalysis.tsx
    │   │   ├── Ecommerce.tsx
    │   │   ├── MetaAds.tsx
    │   │   ├── Feishu.tsx
    │   │   ├── MarketResearch.tsx
    │   │   └── DataIntegration.tsx
    │   ├── components/
    │   └── layouts/
    └── ...
```

**区域划分说明：**

| 区域 | 路径 | 职责 |
|------|------|------|
| 后端 API | `agent/src/api/` | 接收 HTTP 请求，调用 features，返回 JSON |
| 业务逻辑 | `agent/src/features/` | 已有，不直接暴露给前端 |
| 前端 | `agent-web/` | 独立项目，调用后端 API，展示 UI |

---

## 三、后端 API 设计

### 技术栈

- **框架**: FastAPI
- **端口**: 8000
- **CORS**: 允许前端 `http://localhost:3000` 跨域

### 接口列表（按功能模块）

| 模块 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 健康检查 | GET | `/api/health` | 服务是否存活 |
| 页面分析 | POST | `/api/page-analysis/analyze` | 传入 URL，返回 SEO 分析结果 |
| 电商 | GET | `/api/ecommerce/rakuten/orders` | 乐天订单列表 |
| 电商 | POST | `/api/ecommerce/rakuten/sync` | 触发乐天→飞书同步 |
| 电商 | GET | `/api/ecommerce/shopify/...` | Shopify 相关 |
| Meta 广告 | GET | `/api/meta-ads/campaigns` | 广告系列列表 |
| Meta 广告 | GET | `/api/meta-ads/campaigns/{id}/insights` | 广告组洞察 |
| Meta 广告 | POST | `/api/meta-ads/proposals` | 生成优化提案 |
| 飞书 | POST | `/api/feishu/sync` | 手动触发飞书同步 |
| 飞书 | GET | `/api/feishu/status` | 同步状态 |
| 市场分析 | POST | `/api/market-research/analyze` | 提交分析任务 |
| 资料整合 | POST | `/api/data-integration/run` | 执行资料整合 |

### 请求/响应示例

**页面分析：**

```http
POST /api/page-analysis/analyze
Content-Type: application/json

{"url": "https://www.amazon.co.jp/dp/xxx"}

→ 200 OK
{
  "url": "https://...",
  "title": "...",
  "meta_description": "...",
  "h1_list": ["..."],
  "og_title": "...",
  "og_description": "..."
}
```

**乐天同步：**

```http
POST /api/ecommerce/rakuten/sync
Content-Type: application/json

{"date": "2026-02-20"}   // 可选，默认昨日

→ 200 OK
{"success": true, "message": "已同步 123 条记录"}
```

---

## 四、前端设计

### 技术栈建议

- **框架**: React + TypeScript
- **构建**: Vite
- **路由**: React Router
- **UI 组件**: Ant Design / shadcn/ui / Tailwind
- **HTTP 客户端**: axios / fetch

### 页面结构

| 路由 | 页面 | 功能 |
|------|------|------|
| `/` | Dashboard | 总览、快捷入口、最近任务 |
| `/page-analysis` | 页面分析 | 输入 URL，展示 SEO 分析结果 |
| `/ecommerce` | 电商管理 | 乐天/Shopify 切换，订单、库存、同步 |
| `/meta-ads` | Meta 广告 | 广告系列列表、洞察、提案生成与确认 |
| `/feishu` | 飞书同步 | 飞书同步状态、手动触发、配置 |
| `/market-research` | 市场分析 | 关键词/竞品分析、报告生成 |
| `/data-integration` | 资料整合 | 数据源选择、执行、导出 |

### 前端调用后端 API

```typescript
// agent-web/src/api/client.ts
const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export async function analyzePage(url: string) {
  const res = await fetch(`${API_BASE}/api/page-analysis/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  });
  return res.json();
}

export async function triggerRakutenSync(date?: string) {
  const res = await fetch(`${API_BASE}/api/ecommerce/rakuten/sync`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ date }),
  });
  return res.json();
}
```

---

## 五、开发与部署

### 本地开发

```bash
# 终端 1：启动后端
cd c:\Projects\agent
conda activate personal_agent
cd c:\Projects\agent
python run_api.py
# → http://localhost:8000

# 终端 2：启动前端
cd c:\Projects\agent-web
npm install
npm run dev
# → http://localhost:3000
```

### 生产部署（可选）

- 后端：`uvicorn` 或 `gunicorn` 部署到 VPS
- 前端：`npm run build` 后，静态文件部署到 Nginx / Vercel / 飞书云文档

---

## 六、实施步骤建议

1. **Phase 1**: 在 `agent` 下新增 `src/api/`，用 FastAPI 实现 `/api/health` 和 `/api/page-analysis/analyze`
2. **Phase 2**: 创建 `agent-web` 前端项目，实现 Dashboard + 页面分析页
3. **Phase 3**: 按模块逐步补齐后端路由和前端页面
4. **Phase 4**: 接入认证（如需要）、错误处理、日志

---

## 七、总结

| 项目 | 路径 | 职责 |
|------|------|------|
| 后端 | `agent/src/api/` | REST API，调用 features |
| 前端 | `agent-web/` | SPA，调用后端 API |
| 业务逻辑 | `agent/src/features/` | 保持不变，由 API 层调用 |

**区域明确**：后端只负责 API，前端只负责 UI 和调用 API，业务逻辑集中在 `features` 中。
