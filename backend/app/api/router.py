from fastapi import APIRouter

from app.api.routers import auth, chat, copilot, documents, evaluation, health, knowledge

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(chat.router)
api_router.include_router(documents.router)
api_router.include_router(knowledge.router)
api_router.include_router(evaluation.router)
api_router.include_router(copilot.router)
