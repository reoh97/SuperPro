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


def create_combined_app(core, capit=None, side=None, skim=None) -> FastAPI:
    """통합 대시보드 — 코어/폭락/횡보 + 적립금. 한 프로세스에서 구동.

    core: LongTrendTrader / capit: CapitulationTrader / side: SidewaysTrader / skim: ProfitSkim
    """
    app = FastAPI(title="SuperPro 통합 대시보드")
    engines = {"core": core, "capitulation": capit, "sideways": side}

    @app.get("/")
    def index():
        return FileResponse(os.path.join(_HERE, "dashboard.html"))

    @app.get("/api/status")
    def status():
        out = {"core": core.status()}
        if capit is not None:
            out["capitulation"] = capit.status()
        if side is not None:
            out["sideways"] = side.status()
        if skim is not None:
            out["skim"] = skim.status()
        return JSONResponse(out)

    @app.post("/api/{who}/{action}")
    def control(who: str, action: str):
        eng = engines.get(who)
        if eng is None or action not in ("start", "stop"):
            return JSONResponse({"error": "bad request"}, status_code=400)
        (eng.enable if action == "start" else eng.disable)()
        return {"who": who, "running": action == "start"}

    return app
