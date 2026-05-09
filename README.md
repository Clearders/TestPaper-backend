# TestPaper 后端

基于 FastAPI 的后端服务，使用 PostgreSQL 持久化存储，Redis 缓存，以及 Celery 异步任务处理。

前端项目地址(https://github.com/Clearders/TestPapers)

## 数据库

启动应用或运行迁移之前，请先设置 `DATABASE_URL`。后端仅支持 PostgreSQL；SQLite 或未设置 `DATABASE_URL` 时会在启动时被拒绝。

PostgreSQL 连接示例：

```bash
postgresql+psycopg://postgres:password@localhost:5432/testpaper
```

启动 API 之前先执行数据库迁移：

```bash
alembic upgrade head
```

## Redis 与 Celery（异步任务）

Redis 同时用作 **缓存** 和 **Celery 消息代理 / 结果后端**。

### 环境变量

| 变量 | 用途 | 默认值 |
|---|---|---|
| `REDIS_URL` | Redis 连接字符串 | —（API 可选，Celery 必需） |
| `CELERY_BROKER_URL` | Celery 消息代理地址 | 回退到 `REDIS_URL` |
| `CELERY_RESULT_BACKEND` | Celery 结果后端地址 | 回退到 `REDIS_URL` |

示例：

```bash
REDIS_URL=redis://localhost:6379/0
```

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

### 异步任务 API 端点

| 端点 | 说明 |
|---|---|
| `POST /api/v1/tasks/ping` | 探测 Worker（健康检查） |
| `GET /api/v1/tasks/{task_id}` | 轮询任务状态/结果 |
| `POST /api/v1/tasks/export-paper/{paper_id}` | 异步导出试卷 |
| `POST /api/v1/tasks/validate-questions` | 校验全部题目 |
| `POST /api/v1/tasks/validate-question/{question_id}` | 校验单个题目 |
| `GET /api/v1/tasks/stats/questions` | 计算题目统计数据 |
| `POST /api/v1/tasks/cleanup-expired-sessions` | 清理过期的认证令牌 |
| `GET /api/v1/health/redis` | Redis 连通性检查 |

### 快速启动（全部服务）

```bash
# 1. 启动 Redis
redis-server

# 2. 执行数据库迁移
alembic upgrade head

# 3. 启动 Celery Worker（另开终端）
celery -A celery_app worker --loglevel=info --pool=eventlet

# 4. 启动 FastAPI 服务
uvicorn app:app --reload
```

## 运行（仅 API）

```bash
uvicorn app:app --reload
```

## 注意事项

- 数据库表由 Alembic 迁移管理，不会在应用启动时自动创建。
- 初始迁移包含内置的示例题目。
- 初始迁移包含以下内置用户：
  - `admin` / `admin123`：完全权限，包括用户管理。
  - `teacher` / `teacher123`：题目和试卷的编辑权限。
  - `viewer` / `viewer123`：只读权限，无法查看答案。
- 公开注册接口为 `POST /api/v1/auth/register`，注册后将创建激活的教师账户。
- 在所有环境中将 `DATABASE_URL` 指向你的 PostgreSQL 实例，并将迁移作为部署环节之一执行。
- Celery Worker 必须能够访问与 API 相同的 `DATABASE_URL`、`REDIS_URL` 和 Python 环境。
- Redis 对核心 API 是可选的——如果 Redis 不可用，相关端点会优雅降级。

