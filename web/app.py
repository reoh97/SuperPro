"""FastAPI 대시보드: 상태 조회 + 매매 시작/정지 제어.

엔드포인트:
  GET  /              대시보드 HTML
  GET  /api/status    현재 상태(JSON)
  POST /api/start     매매 활성화
  POST /api/stop      매매 비활성화
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from trader.engine import Engine

_HERE = os.path.dirname(__file__)


def create_app(engine: Engine) -> FastAPI:
    app = FastAPI(title="Upbit Auto Trader")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(_HERE, "index.html"))

    @app.get("/api/status")
    def status():
        return JSONResponse(engine.snapshot())

    @app.post("/api/start")
    def start():
        engine.enable()
        return {"running": True}

    @app.post("/api/stop")
    def stop():
        engine.disable()
        return {"running": False}

    return app


def create_combined_app(satellite, core) -> FastAPI:
    """좌(새틀라이트)·우(코어) 2엔진 통합 대시보드. 두 엔진을 한 프로세스에서 구동.

    satellite: LiveTrader (snapshot())  /  core: LongTrendTrader (status())
    """
    app = FastAPI(title="SuperPro — 코어/새틀라이트 통합 대시보드")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(_HERE, "dashboard.html"))

    @app.get("/api/status")
    def status():
        return JSONResponse({"satellite": satellite.snapshot(), "core": core.status()})

    @app.post("/api/{who}/{action}")
    def control(who: str, action: str):
        eng = satellite if who == "satellite" else core if who == "core" else None
        if eng is None or action not in ("start", "stop"):
            return JSONResponse({"error": "bad request"}, status_code=400)
        (eng.enable if action == "start" else eng.disable)()
        return {"who": who, "running": action == "start"}

    return app
