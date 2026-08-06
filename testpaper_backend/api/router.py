from __future__ import annotations

from fastapi import APIRouter

from testpaper_backend.api.routes import auth, banks, drafts, health, images, meta, papers, questions, root, tasks, users, websocket

router = APIRouter()
router.include_router(root.router)
router.include_router(auth.router)
router.include_router(websocket.router)
router.include_router(users.router)
router.include_router(meta.router)
router.include_router(images.router)
router.include_router(questions.router)
router.include_router(papers.router)
router.include_router(drafts.router)
router.include_router(banks.router)
router.include_router(health.router)
router.include_router(tasks.router)
