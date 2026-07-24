"""KI Trader (AI Trading Engine) Endpoints."""
import json
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from core.auth import require_admin
from services.ai_engine import ai_engine
from services.news_feed import news_feed

router = APIRouter(tags=["ai"])


@router.get("/api/ai/status")
async def ai_status():
    return ai_engine.status()


@router.post("/api/ai/config")
async def ai_config(updates: Dict, _: bool = Depends(require_admin)):
    cfg = await ai_engine.update_config(updates)
    return {"status": "success", "config": cfg}


@router.post("/api/ai/analyze")
async def ai_analyze_now(_: bool = Depends(require_admin)):
    result = await ai_engine.run_analysis(manual=True)
    return result


@router.get("/api/ai/chat/history")
async def ai_chat_history(limit: int = 80):
    return {"messages": await ai_engine.chat_history(limit)}


@router.post("/api/ai/chat")
async def ai_chat(body: Dict, _: bool = Depends(require_admin)):
    text = (body.get("message") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Nachricht fehlt")

    async def gen():
        try:
            async for token in ai_engine.chat_stream(text):
                yield f"data: {json.dumps({'t': token})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)[:200]})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.delete("/api/ai/chat")
async def ai_chat_clear(_: bool = Depends(require_admin)):
    await ai_engine.clear_chat()
    return {"status": "success"}


@router.get("/api/ai/news")
async def ai_news(limit: int = 20):
    return {"headlines": await news_feed.get_headlines(limit)}
