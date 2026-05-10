# TestPaper 后端

基于 FastAPI 的后端服务，使用 PostgreSQL 持久化存储，Redis 缓存，以及 Celery 异步任务处理。提供 RESTful API 供前端 TestPapers 调用。

前端项目地址：<https://github.com/Clearders/TestPapers>

## 项目结构

```
TestPaper-backend/
├── app.py                 # FastAPI 主应用，包含所有路由处理及遗传算法组卷逻辑
├── main.py                # 入口文件（启动 uvicorn）
├── app_factory.py         # FastAPI 应用工厂，CORS 中间件配置
├── settings.py            # 环境变量配置读取
├── db.py                  # SQLAlchemy ORM 模型定义与数据库会话
├── schemas.py             # Pydantic 请求/响应模型（20+ 数据模型）
├── repositories.py        # QuestionStore 与 PaperStore（类字典缓存接口）
├── security.py            # 认证、密码哈希、权限检查
├── tasks.py               # Celery 异步任务定义（7 个任务）
├── celery_app.py          # Celery 应用配置
├── redis_client.py        # Redis 客户端与缓存工具
├── time_utils.py          # UTC 时间工具
├── pyproject.toml         # 项目元数据与依赖
├── alembic.ini            # Alembic 数据库迁移配置
├── test_main.http         # HTTP 测试文件（VS Code REST Client）
└── alembic/
    ├── env.py             # Alembic 运行环境配置
    ├── script.py.mako     # 迁移脚本模板
    └── versions/
        ├── 20260507_0001_initial_schema.py         # 初始化：用户、试题、试卷表 + 种子数据
        ├── 20260508_0002_personal_questions_and_images.py  # 添加 owner_id 和 images 字段
        └── 20260509_0003_json_columns_to_jsonb.py  # JSON 列转换为 JSONB 类型
```

## 技术栈

| 组件 | 技术 | 用途 |
|------|------|------|
| Web 框架 | FastAPI ≥ 0.136 | RESTful API 服务 |
| ORM | SQLAlchemy ≥ 2.0 | 数据库操作 |
| 数据库 | PostgreSQL | 持久化存储 |
| 数据库驱动 | psycopg[binary] ≥ 3.2 | PostgreSQL 连接 |
| 迁移工具 | Alembic ≥ 1.17 | 数据库版本管理 |
| 缓存/消息 | Redis ≥ 5.2 | 缓存、Celery broker、结果后端 |
| 任务队列 | Celery[redis] ≥ 5.5 | 异步任务处理 |
| ASGI 服务器 | uvicorn ≥ 0.46 | HTTP 服务运行 |
| 密码哈希 | PBKDF2-SHA256 | 120,000 次迭代 |
| 开发工具 | pytest ≥ 9.0, ruff ≥ 0.14 | 测试、代码检查 |

## 数据库

启动应用或运行迁移之前，请先设置 `DATABASE_URL`。后端仅支持 PostgreSQL。

默认连接：
```
postgresql+psycopg://postgres:ABCdefg123@localhost:5432/postgres
```

启动 API 之前先执行数据库迁移：

```bash
alembic upgrade head
```

### 数据库表

| 表名 | 说明 | 主要字段 |
|------|------|------|
| `users` | 用户账户 | id, username, displayName, passwordHash, role, isActive |
| `auth_tokens` | 认证令牌 | token, user_id, created_at, expires_at |
| `questions` | 试题 | id, type, subject, difficulty, tags, text, options, answer, has_latex, images, owner_id |
| `papers` | 试卷 | id, title, subject, duration, totalMarks, status |
| `paper_questions` | 试卷-试题关联 | paper_id, question_id, orderNo, marks |

### 预置数据

初始迁移包含：
- **10 道示例试题**：涵盖数学、物理、化学，包含选择题、填空题、简答题
- **3 个内置用户**：

| 用户名 | 密码 | 角色 | 权限 |
|--------|------|------|------|
| `admin` | `admin123` | 管理员 | 完全权限，包括用户管理 |
| `teacher` | `teacher123` | 教师 | 题目和试卷的读写权限 |
| `viewer` | `viewer123` | 观察者 | 只读权限，无法查看答案 |

## Redis 与 Celery（异步任务）

Redis 同时用作 **缓存** 和 **Celery 消息代理 / 结果后端**。默认连接 `redis://localhost:6379/0`。

### 环境变量

| 变量 | 用途 | 默认值 |
|------|------|------|
| `DATABASE_URL` | PostgreSQL 连接字符串 | `postgresql+psycopg://postgres:ABCdefg123@localhost:5432/postgres` |
| `REDIS_URL` | Redis 连接字符串 | `redis://localhost:6379/0` |
| `CORS_ORIGINS` | CORS 允许的来源（逗号分隔） | `localhost:3000,127.0.0.1:3000,localhost:3001,127.0.0.1:3001` |
| `AUTH_COOKIE_NAME` | 认证 Cookie 名称 | `testpapers_session` |
| `AUTH_COOKIE_DOMAIN` | Cookie 域名 | (无) |
| `AUTH_COOKIE_SECURE` | 是否仅 HTTPS 传输 Cookie | `false` |
| `AUTH_COOKIE_SAMESITE` | SameSite 属性 | `lax` |
| `CELERY_BROKER_URL` | Celery 消息代理地址 | 回退到 `REDIS_URL` |
| `CELERY_RESULT_BACKEND` | Celery 结果后端地址 | 回退到 `REDIS_URL` |

### 启动 Celery Worker

```bash
celery -A celery_app worker --loglevel=info --concurrency=4
```

Windows 下（使用 eventlet 支持并发）：

```bash
celery -A celery_app worker --loglevel=info --pool=eventlet --concurrency=4
```

### 启动 Celery Beat（定时任务）

```bash
celery -A celery_app beat --loglevel=info
```

## API 端点

### 认证 (`/api/v1/auth/`)

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|:---:|
| `POST` | `/login` | 用户名密码登录，设置 HttpOnly Cookie | 否 |
| `POST` | `/register` | 公开注册（创建教师账户） | 否 |
| `GET` | `/me` | 获取当前用户信息 | 是 |
| `POST` | `/refresh` | 刷新会话令牌 | 是 |
| `POST` | `/logout` | 登出，清除会话和 Cookie | 是 |

### 用户管理 (`/api/v1/users/`) — 需 `users:manage` 权限

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | 列出所有用户 |
| `POST` | `/` | 创建用户 |
| `PATCH` | `/{user_id}` | 更新用户（角色、密码、启用状态） |
| `DELETE` | `/{user_id}` | 删除用户 |

### 试题管理 (`/api/v1/questions/`)

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| `GET` | `/` | 试题列表（分页、搜索、筛选） | `questions:read` |
| `GET` | `/mine` | 当前用户的个人题库 | `questions:read` |
| `GET` | `/{question_id}` | 试题详情 | `questions:read` |
| `POST` | `/` | 创建试题 | `questions:write` |
| `PATCH` | `/{question_id}` | 更新试题 | `questions:write` |
| `DELETE` | `/{question_id}` | 删除试题 | `questions:delete` |

筛选参数：`q`（全文搜索）、`subject`、`difficulty`、`type`、`tags`、`hasLatex`、`ownerId`、`includeAnswer`，以及分页排序参数。

### 试卷管理 (`/api/v1/papers/`)

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| `POST` | `/` | 创建试卷 | `papers:write` |
| `POST` | `/generate` | 遗传算法自动组卷 | `papers:write` |
| `GET` | `/{paper_id}` | 试卷详情（可选展开试题） | `papers:read` |
| `PATCH` | `/{paper_id}` | 更新试卷元数据 | `papers:write` |
| `POST` | `/{paper_id}/questions` | 向试卷添加试题 | `papers:write` |
| `DELETE` | `/{paper_id}/questions/{question_id}` | 从试卷移除试题 | `papers:write` |
| `PUT` | `/{paper_id}/questions/order` | 调整试题排序 | `papers:write` |
| `POST` | `/{paper_id}/export-preview` | 导出预览 | `papers:read` |

### 图片上传 (`/api/v1/images/`)

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| `POST` | `/upload` | 上传试题配图（PNG/JPEG/GIF/WebP/SVG） | `questions:write` |

### 元数据 (`/api/v1/meta/`)

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| `GET` | `/subjects` | 获取所有学科列表 | `questions:read` |
| `GET` | `/tags` | 获取所有标签列表 | `questions:read` |

### 异步任务 (`/api/v1/tasks/`)

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| `POST` | `/ping` | Worker 健康检查 | `questions:read` |
| `GET` | `/{task_id}` | 查询任务状态和结果 | `questions:read` |
| `POST` | `/export-paper/{paper_id}` | 异步导出试卷 | `papers:read` |
| `POST` | `/validate-questions` | 验证全部试题 | `questions:read` |
| `POST` | `/validate-question/{question_id}` | 验证单个试题 | `questions:read` |
| `POST` | `/cleanup-expired-sessions` | 清理过期认证令牌 | `users:manage` |
| `GET` | `/stats/questions` | 计算试题统计信息 | `questions:read` |

### WebSocket (`/api/v1/ws`)

实时事件推送，使用 Cookie/Bearer 认证。事件类型：
- `auth.connected` — 连接成功
- `question.created` / `question.updated` / `question.deleted`
- `paper.created` / `paper.updated`
- `paper.questions.added` / `paper.question.removed` / `paper.questions.reordered`

### 健康检查 (`/api/v1/health/`)

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/redis` | Redis 连通性检查 |

## 认证机制

- **密码存储**：PBKDF2-SHA256，120,000 次迭代
- **会话令牌**：48 字节 URL-safe 随机字符串，存储于 `auth_tokens` 表
- **会话有效期**：12 小时
- **Cookie 名称**：`testpapers_session`（可通过环境变量配置）
- **Cookie 属性**：`HttpOnly`、`Secure`（生产环境）、`SameSite=Lax`
- **兼容降级**：非浏览器客户端可使用 `Authorization: Bearer <token>` 请求头

## 权限模型

| 权限标识 | 说明 | admin | teacher | viewer |
|------|------|:---:|:---:|:---:|
| `questions:read` | 查看试题 | ✓ | ✓ | ✓ |
| `questions:write` | 创建/编辑试题 | ✓ | ✓ | ✗ |
| `questions:delete` | 删除试题 | ✓ | ✓ | ✗ |
| `answers:read` | 查看答案 | ✓ | ✓ | ✗ |
| `papers:read` | 查看试卷 | ✓ | ✓ | ✓ |
| `papers:write` | 创建/编辑试卷 | ✓ | ✓ | ✗ |
| `users:manage` | 管理用户 | ✓ | ✗ | ✗ |

## 试题类型

| 类型 | 常量值 | 说明 |
|------|------|------|
| 选择题 | `choice` | 需要 `options` 数组 |
| 判断题 | `true_false` | 固定 `["True", "False"]` 选项 |
| 填空题 | `blank` | 不可有 `options` |
| 简答题 | `short_answer` | 不可有 `options` |
| 解答题 | `essay` | 可配置 `essayBlankSpace`（行数和行高） |

### 难度等级

| 难度 | 算法权重 |
|------|:---:|
| `easy` (简单) | 1.0 |
| `medium` (中等) | 1.5 |
| `hard` (困难) | 2.0 |

## 遗传算法自动组卷

通过 `POST /api/v1/papers/generate` 实现，使用遗传算法从题库中自动生成试卷：

1. **选择**：对候选试题进行遗传算法迭代
2. **约束**：目标难度分布、题型分布、必选标签覆盖、总分限制
3. **优化**：最小化约束惩罚，最大化标签多样性
4. **诊断**：返回适应度得分、覆盖率分析

可调参数：种群大小、迭代代数、交叉率、变异率、精英保留数、锦标赛规模、随机种子。

## Celery 异步任务

| 任务 | 说明 |
|------|------|
| `ping` | 健康检查 |
| `compute_question_stats` | 按题型/难度/学科聚合统计 |
| `export_paper` | 导出试卷（JSON/CSV/TXT） |
| `validate_question` | 校验单个试题数据 |
| `validate_all_questions` | 并行校验全部试题（Celery group） |
| `detect_latex_questions` | 识别含 LaTeX 公式的试题 |
| `cleanup_expired_sessions` | 清理过期认证令牌 |

## 快速启动

```bash
# 1. 配置环境变量
export DATABASE_URL="postgresql+psycopg://postgres:password@localhost:5432/testpapers"
export REDIS_URL="redis://localhost:6379/0"

# 2. 执行数据库迁移
alembic upgrade head

# 3. 启动 Redis
redis-server

# 4. 启动 Celery Worker（另开终端）
celery -A celery_app worker --loglevel=info --pool=eventlet

# 5. 启动 FastAPI 服务
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

仅运行 API（无需 Celery/Redis）：

```bash
uvicorn app:app --reload
```

## 注意事项

- 数据库表由 Alembic 迁移管理，不会在应用启动时自动创建。
- 初始迁移包含内置示例试题和用户。
- 公开注册接口 `POST /api/v1/auth/register` 创建激活的教师账户。
- 在所有环境中将 `DATABASE_URL` 指向你的 PostgreSQL 实例。
- Celery Worker 必须能访问与 API 相同的 `DATABASE_URL`、`REDIS_URL` 和 Python 环境。
- Redis 对核心 API 是可选的——如果 Redis 不可用，相关端点会优雅降级。

## API 文档

完整的 API 接口文档请参阅前端项目中的 [docs/api-spec.md](../TestPapers/docs/api-spec.md)。

