# PR #8 / CLE-18 合并后部署检查清单

> 适用范围：TestPaper-backend PR #8 "Add native Bearer auth with device sessions + TeX export" 合并后，生产环境部署与验证。
> 变更摘要：新增原生客户端 Bearer 认证（`/auth/token` 等 4 个端点）、`auth_tokens` 表扩展 + `auth_audit_log` 新表（迁移 `20260804_0016`）、论文/草稿下载支持 `format=tex`。
> 部署方式：参照 `DEPLOYMENT-debian-production.md`（systemd + uv + alembic）。下文命令中的路径/用户按该文档的占位符示例。

---

## 0. 合并前（前置检查）

- [ ] PR #8 的 GitHub 检查项 `checks` 为 success，`mergeable_state` 为 clean
- [ ] 下游影响已确认：Web（TestPapers）无破坏性影响；Web 已实现 `format=tex` 下载，本次为配套修复
- [ ] 本地质量门禁已过：`scripts/check.py`（ruff / pytest 138 passed / OpenAPI drift / 迁移模拟 16 revisions clean）

## 1. 合并 PR

- [ ] 通过 GitHub 合并 PR #8 到 `master`（squash 或常规合并均可；CI 已绿）
- [ ] 合并后确认 master HEAD 为 `1a3818b`（或包含该提交的合并提交）

## 2. 后端更新与依赖

按 `DEPLOYMENT-debian-production.md` §11 后端更新流程：

- [ ] `git pull`（部署目录 `/srv/testpaper/TestPaper-backend`）拉取到新 master
- [ ] `uv sync --no-dev`（生产依赖；如声明 Python >=3.14 需先 `uv python install 3.14`）
- [ ] 确认新配置项生效（默认值即可，无需显式配置）：
  - `ACCESS_TOKEN_TTL_MINUTES`（默认 30）
  - `REFRESH_TOKEN_TTL_DAYS`（默认 30）

## 3. 数据库迁移（关键步骤）

- [ ] 执行 `uv run alembic upgrade head`（应用迁移 `20260804_0016`，down_revision `20260702_0015`）
- [ ] 迁移后验证 `auth_tokens` 新增列齐全：
  - `tokenType`（默认 `session`）、`deviceId`、`deviceName`、`ipAddress`、`userAgent`、`lastSeenAt`、`refreshTokenId`
- [ ] 验证新表 `auth_audit_log` 存在（列：`id`/`userId`/`deviceId`/`event`/`ipAddress`/`created_at`，外键 `users.id` ON DELETE CASCADE）
- [ ] 验证新索引：`ix_auth_tokens_deviceId`、`ix_auth_tokens_refreshTokenId`、`ix_auth_audit_log_userId`、`ix_auth_audit_log_event`
- [ ] 存量数据检查：已有 session 行的 `tokenType = 'session'`（server_default 已回填），登录会话不受影响

## 4. 服务重启与健康检查

- [ ] `sudo systemctl restart testpaper-backend testpaper-celery`
- [ ] `systemctl status` 均为 running（Restart=always 兜底）
- [ ] `curl http://127.0.0.1:8000/api/v1/health/redis` 返回 ok
- [ ] 日志无迁移/启动错误：`sudo journalctl -u testpaper-backend -f`
- [ ] 通过 Nginx 公共入口 `curl https://<domain>/api/v1/health/redis` 正常

## 5. 认证 API 冒烟（新增端点）

用管理员账号（`scripts/bootstrap_admin.py` 创建）验证：

- [ ] `POST /api/v1/auth/token`
  ```bash
  curl -s -X POST http://127.0.0.1:8000/api/v1/auth/token \
    -H 'Content-Type: application/json' \
    -d '{"username":"admin","password":"<密码>","deviceId":"deploy-check","deviceName":"Deploy Check"}'
  ```
  预期：200，返回 `TokenPair`（accessToken / refreshToken / expiresIn=1800 / refreshExpiresIn=2592000 / user），**不设置 Cookie**
- [ ] `POST /api/v1/auth/token/refresh`
  ```bash
  curl -s -X POST http://127.0.0.1:8000/api/v1/auth/token/refresh \
    -H 'Content-Type: application/json' -d '{"refreshToken":"<上一步的refreshToken>"}'
  ```
  预期：200，返回新的 TokenPair（旧 refresh 及其关联 access 已撤销）
- [ ] 安全约束：用 refresh token 作 `Authorization: Bearer` 访问任意受保护端点
  预期：401，`INVALID_TOKEN`（refresh 不得作为访问凭据）
- [ ] Web 现有登录回归：`POST /api/v1/auth/login` 仍返回 `Set-Cookie: testpapers_session=...`（cookie 会话路径不受影响）

## 6. 设备会话与审计验证

- [ ] `GET /api/v1/auth/devices`（带上一步 access token 的 Bearer）返回设备列表，`deploy-check` 设备标记 `current: true`
- [ ] `DELETE /api/v1/auth/devices/{device_id}` 撤销非当前设备 → 预期 204
- [ ] 对当前设备执行 `DELETE /api/v1/auth/devices/{device_id}` → 预期 409 `DEVICE_IS_CURRENT`
- [ ] 改密联动：`POST /api/v1/auth/change-password` 后，其他会话被撤销、当前会话保留；`auth_audit_log` 写入 `password_changed`（含 ipAddress）
- [ ] 删号联动：`DELETE /api/v1/auth/account` 后该用户所有 token 撤销；`auth_audit_log` 写入 `account_deleted`

## 7. TEX 导出验证（Web 联动，本次修复点）

- [ ] `GET /api/v1/papers/{paper_public_id}/download?format=docx` 仍正常返回 docx（既有行为回归）
- [ ] `GET /api/v1/papers/{paper_public_id}/download?format=tex` 返回 200，`Content-Type: application/x-tex`，响应头 `X-Export-Format: tex`，内容为 TEX 源码
- [ ] `POST /api/v1/papers/draft-download?format=tex`（body 为 `PaperDraftDownloadRequest`）返回 TEX
- [ ] `GET /api/v1/drafts/{draft_public_id}/download?format=tex` 返回 TEX
- [ ] 非法格式（如 `format=pdf`）→ 422（pattern 校验仍生效）
- [ ] Web 端实际操作：在 TestPapers 前端导出面板选择 TEX 下载正式试卷/云草稿，文件可正常打开

## 8. Web 侧配套动作（非阻塞）

- [ ] （可选）更新 `TestPapers/e2e/backend.lock.json` 的 `commit` 到后端新 master，使 full-stack e2e 覆盖 TEX 流程（当前锁为 `983846ad`）
- [ ] （单独 PR）Web 仓库 `docs/api-spec.md` 补充 "Native Client Authentication (Bearer Tokens)" 章节
- [ ] （可选）Web 契约锁：`contract.lock.json` 绑定 `api-v1.0.0` tag，本次不受影响；若后续发布新契约版本需同步更新

## 9. 契约/版本注意项

- [ ] 本次 PR 新增 4 个端点但 `info.version` 仍为 `1.0.0`（未 bump）。新增端点不触发 breaking，可正常发布；建议按项目版本策略在下一次发版时 bump minor 并打 `api-v1.1.0` tag，供 Web 契约锁引用

## 10. 回滚预案

- [ ] 数据库回滚（如遇问题）：
  ```bash
  uv run alembic downgrade 20260702_0015   # 撤销 20260804_0016（删新表、删新列、删索引）
  ```
- [ ] 代码回滚：`git revert` 或回退到上一发布提交后，重新 `uv sync --no-dev` 并重启服务
- [ ] 回滚验证：`auth_tokens` 恢复原结构、`auth_audit_log` 删除、Web 下载 `format=tex` 重新返回 422（回到旧行为，Web 前端需同步回退 TEX 入口）
- [ ] 灰度建议：先在预发环境跑一遍 §3–§7 清单，再对生产执行
