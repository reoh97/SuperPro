"""FastAPI 대시보드: 상태 조회 + 매매 시작/정지 제어.

엔드포인트:
  GET  /              대시보드 HTML
  GET  /api/status    현재 상태(JSON)
  POST /api/start     매매 활성화
  POST /api/stop      매매 비활성화
  POST /api/resume    목표수익 정지 해제 + 재개(기준선 리셋)
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

    @app.post("/api/resume")
    def resume():
        # 목표수익 도달로 멈춘 상태를 풀고 재개(기준선 리셋). LiveTrader만 지원.
        fn = getattr(engine, "resume", None)
        if callable(fn):
            fn()
            return {"running": engine.is_enabled()}
        engine.enable()
        return {"running": True}

    return app
