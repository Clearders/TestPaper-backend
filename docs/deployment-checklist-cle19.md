# CLE-19 合并后部署检查清单

> 适用范围：TestPaper-backend CLE-19 "共享题库领域模型与权限策略"（分支 `cle-19-question-bank`）合并后，生产环境部署与验证。
> 变更摘要：新增共享题库聚合实体（`question_banks`/`question_bank_items`/`question_bank_members`/`bank_publications`/`bank_subscriptions` 五表，迁移 `20260805_0017`）、`/api/v1/banks` 全端点、5 个 `banks:*` 权限、不可变发布快照（版本化）、订阅与分叉（含答案脱敏 `load_bank_snapshot`）。
> 部署方式：参照 `DEPLOYMENT-debian-production.md`（systemd + uv + alembic）。下文命令中的路径/用户按该文档的占位符示例。

---

## 0. 合并前（前置检查）

- [ ] CLE-19 PR 的 GitHub 检查项 `checks` 为 success（ruff / pytest / OpenAPI drift / 迁移模拟 17 revisions clean / PostgreSQL smoke round-trip）
- [ ] 本地质量门禁已过：`scripts/check.py`（170 passed）
- [ ] 前端契约：`TestPapers/contracts/openapi.json` 已同步（含 `/api/v1/banks`），`contract.lock.json` sha256 已更新；`cloud-api.d.ts` 已重新生成；`npm run verify` 通过

## 1. 合并 PR

- [ ] 通过 GitHub 合并 CLE-19 PR 到 `master`
- [ ] 合并后确认 `contracts/openapi.json` 的 `info.version`（保持 `1.0.0`；本次为向后兼容新增端点）

## 2. 后端更新与依赖

按 `DEPLOYMENT-debian-production.md` §11 后端更新流程：

- [ ] `git pull`（部署目录 `/srv/testpaper/TestPaper-backend`）拉取到新 master
- [ ] `uv sync --no-dev`

## 3. 数据库迁移（关键步骤）

- [ ] 执行 `uv run alembic upgrade head`（应用迁移 `20260805_0017`，down_revision `20260804_0016`）
- [ ] 验证五张新表存在：
  - `question_banks`（列：`id`/`publicId`/`name`/`description`/`ownerId`/`visibility`/`latestVersion`/`created_at`/`updated_at`）
  - `question_bank_items`（复合主键 `(bankId, questionId)`，外键 `question_banks.id`/`questions.id` ON DELETE CASCADE）
  - `question_bank_members`（复合主键 `(bankId, userId)`）
  - `bank_publications`（`UniqueConstraint(bankId, version)`，`state` JSONB）
  - `bank_subscriptions`（复合主键 `(bankId, userId)`）
- [ ] 验证新索引：`ix_question_banks_*`、`ix_question_bank_items_bankId`、`ix_question_bank_members_bankId`、`ix_bank_publications_*`、`ix_bank_subscriptions_bankId`
- [ ] 存量数据检查：`question_banks` 为空表（新功能），无回填需求；`latestVersion` 默认 0

## 4. 服务重启与健康检查

- [ ] `sudo systemctl restart testpaper-backend testpaper-celery`
- [ ] `systemctl status` 均为 running
- [ ] `curl http://127.0.0.1:8000/api/v1/health/redis` 返回 ok
- [ ] 日志无迁移/启动错误：`sudo journalctl -u testpaper-backend -f`

## 5. 权限与可见性冒烟（新增端点）

用管理员 + 普通教师/查看者账号验证：

- [ ] 权限矩阵：`GET /api/v1/auth/me` 返回的 `permissions` 含 `banks:read/write/delete/publish/subscribe`（admin/teacher）；viewer 仅含 `banks:read`、`banks:subscribe`
- [ ] 创建题库：`POST /api/v1/banks`（`{name, description?, visibility?}`）→ 201，默认 `private`，`accessRole=owner`
- [ ] 可见性过滤：`GET /api/v1/banks` 仅返回「我拥有 / 我成员 / public」的题库；非成员的 `private` 题库不出现
- [ ] 越权伪装：非成员 `GET /api/v1/banks/{private_id}` → 404 `BANK_NOT_FOUND`（不泄露存在性）
- [ ] 成员管理：owner/admin 添加成员（`POST /banks/{id}/members`，username+role）；owner 作为成员 → 422 `BANK_OWNER_CANNOT_BE_MEMBER`；重复添加 → 422 `BANK_MEMBER_EXISTS`；移除后成员再读 `team` 题库 → 404
- [ ] 加题：`POST /banks/{id}/items`（`questionIds[]`）→ 请求内重复 422 `VALIDATION_ERROR`；库内已存在 409 `BANK_ITEM_EXISTS`（`details.questionPublicIds` 列出冲突项）

## 6. 发布/版本/脱敏验证（关键）

- [ ] 空题库发布 → 422 `BANK_PUBLISH_EMPTY`
- [ ] `POST /banks/{id}/publish` → 成功，`version=1`；`GET /banks/{id}/versions` 列出 v1；`GET /banks/{id}/versions/1` 返回快照 `state`（含 `items[].data` 完整题目内容）
- [ ] 稳定版本：发布后编辑题库题目，再 `GET /banks/{id}/versions/1` → 仍是发布时的内容（不随编辑变化）
- [ ] 重复发布 → 409 `BANK_ALREADY_PUBLISHED`；`POST /banks/{id}/withdraw` → 撤销最新版；未发布撤回 → 409 `BANK_NOT_PUBLISHED`
- [ ] 答案脱敏：无 `answers:read` 的用户 `GET /banks/{id}/versions/1` → `items[].data.answer` 为 `[redacted]`（multiple_choice 为 `["[redacted]"]`）；有权限用户看到真实答案；快照原始数据未被修改

## 7. 订阅与分叉验证（关键）

- [ ] 订阅 `public` 题库：`POST /banks/{id}/subscribe` → 成功；`DELETE /banks/{id}/subscribe` → 204
- [ ] 订阅 `private` 题库 → 422 `BANK_SUBSCRIBE_PRIVATE`
- [ ] 分叉：`POST /banks/{id}/fork`（`{version?}`）→ 201，新 `private` 题库 owner 为当前用户，题目为快照复制（新 publicId）
- [ ] 分叉独立性：改分叉库题目不影响原库，改原库不影响分叉库
- [ ] 脱敏后门验证：无 `answers:read` 用户 fork → 分叉出的题目 `answer` 为 `[redacted]`（与读版本同经 `load_bank_snapshot` 统一脱敏）

## 8. Web 侧配套动作

- [ ] `TestPapers` 前端已含「共享题库」tab（`SharedBanksPanel`/`QuestionBankCard`）；部署后冒烟：创建题库 → 加题 → 发布 → 订阅 → 分叉
- [ ] （单独 PR 发布契约时）`contract.lock.json` 的 `source.commit` 需更新为后端 master 上包含该契约的提交 SHA（当前为分支头 `c7a7008` 的临时值）
- [ ] （可选）`TestPapers/e2e/backend.lock.json` 的 `commit` 更新到后端新 master，使 full-stack e2e 覆盖 banks 流程

## 9. 契约/版本注意项

- [ ] 新增 `/api/v1/banks` 端点但 `info.version` 仍为 `1.0.0`（未 bump）；建议按项目版本策略在下次发版时 bump minor 并打 `api-v1.1.0` tag，供 Web 契约锁引用

## 10. 回滚预案

- [ ] 数据库回滚（如遇问题）：
  ```bash
  uv run alembic downgrade 20260804_0016   # 撤销 20260805_0017（删五张新表与索引）
  ```
- [ ] 代码回滚：`git revert` 或回退到上一发布提交后，重新 `uv sync --no-dev` 并重启服务
- [ ] 回滚验证：五张表删除、`/api/v1/banks` 返回 404、`banks:*` 权限从角色权限集移除
- [ ] 灰度建议：先在预发环境跑一遍 §3–§7 清单，再对生产执行
