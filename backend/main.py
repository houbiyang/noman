"""现有 Unity 演示功能的 HTTP 入口，默认只监听本机。"""

from contextlib import asynccontextmanager
import argparse
import logging
from threading import Event, Thread
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
import psycopg
import uvicorn

from backend.contracts import CommandRequest, ConditionPreview, CreateGame, SessionRequest, PreparationPreview
from backend.demo_domain import map_manifest
from backend.runtime import Settings, ServiceError, initialize_runtime
from backend.service import SandboxService


def create_app(settings: Settings) -> FastAPI:
    """以显式配置构造应用，导入不会建库、创建对局或启动线程。"""
    service = SandboxService(settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        """后台播放只提交服务端命令，关闭应用时停止本进程节奏。"""
        stopped = Event()

        def playback():
            """每个节奏重新读取数据库，不缓存可写状态或旧控制依据。"""
            while not stopped.wait(settings.demo_tick_seconds):
                try:
                    service.tick()
                except (psycopg.Error, ServiceError) as error:
                    logging.getLogger(__name__).error("后台播放暂未提交：%s", type(error).__name__)

        worker = Thread(target=playback, name="sandbox-playback", daemon=True)
        if settings.scheduler_enabled:
            worker.start()
        yield
        stopped.set()
        if worker.is_alive():
            worker.join(timeout=1)

    app = FastAPI(title="穹图现有功能后端", version="1.0.0", lifespan=lifespan,
                  description="固定演示、持久化及角色隔离；正式 AI、地形规则与交战裁决尚未实现。")
    app.state.service = service
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:18766", "http://localhost:18766"],
                       allow_methods=["GET", "POST", "DELETE"],
                       allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Bootstrap-Key"])

    @app.middleware("http")
    async def private_responses(request: Request, call_next):
        """业务响应不进入浏览器或代理缓存。"""
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ServiceError)
    async def business_error(request: Request, error: ServiceError):
        return JSONResponse(status_code=error.status, content={"error": {"code": error.code, "message": error.message}})

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, error: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": {"code": "INPUT_INVALID", "message": "参数类型、范围或字段不符合接口契约"}})

    @app.exception_handler(psycopg.Error)
    async def database_error(request: Request, error: psycopg.Error):
        logging.getLogger(__name__).error("数据库请求未完成：%s", type(error).__name__)
        return JSONResponse(status_code=503, content={"error": {"code": "DATABASE_UNAVAILABLE", "message": "数据库暂未完成请求，请按原请求键核对结果"}})

    def bearer(authorization: Annotated[str | None, Header()] = None) -> str:
        """只接受请求头中的席位令牌。"""
        if not authorization or not authorization.startswith("Bearer ") or not authorization[7:].strip():
            raise ServiceError(401, "SESSION_REQUIRED", "请提供有效席位 Bearer 凭据")
        return authorization[7:]

    def bootstrap(x_bootstrap_key: Annotated[str, Header()] = "") -> None:
        service.bootstrap(x_bootstrap_key)

    token = Annotated[str, Depends(bearer)]
    request_key = Annotated[UUID, Header(alias="Idempotency-Key")]

    @app.get("/api/v1/health")
    def health():
        result = service.health()
        return JSONResponse(status_code=200 if result["database_ready"] else 503, content=result)

    @app.get("/api/v1/bootstrap/games", dependencies=[Depends(bootstrap)])
    def bootstrap_history(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        return service.history(offset=offset, limit=limit)

    @app.post("/api/v1/games", dependencies=[Depends(bootstrap)])
    def create_game(body: CreateGame, idempotency_key: request_key):
        result, replayed = service.create_game(body, idempotency_key)
        return JSONResponse(status_code=200 if replayed else 201, content=jsonable_encoder(dict(**result, replayed=replayed)))

    @app.get("/api/v1/game-creations/{creation_key}", dependencies=[Depends(bootstrap)])
    def creation(creation_key: UUID):
        return service.creation(creation_key)

    @app.post("/api/v1/bootstrap/games/{game_id}/sessions", dependencies=[Depends(bootstrap)])
    def issue_session(game_id: UUID, body: SessionRequest):
        return service.issue_session(str(game_id), body.role)

    @app.get("/api/v1/session")
    def current_session(access_token: token):
        return service.session(access_token)

    @app.delete("/api/v1/session")
    def revoke_session(access_token: token):
        return service.revoke_session(access_token)

    @app.get("/api/v1/games")
    def history(access_token: token, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        return service.history(bearer=access_token, offset=offset, limit=limit)

    @app.get("/api/v1/maps/current")
    def current_map(access_token: token):
        service.session(access_token)
        return map_manifest()

    @app.get("/api/v1/games/{game_id}/view")
    def view(game_id: UUID, access_token: token):
        return service.view(str(game_id), access_token)

    @app.get("/api/v1/games/{game_id}/routes")
    def routes(game_id: UUID, access_token: token, unit_type: str = Query(..., min_length=1, max_length=20),
               from_node_id: str = Query(..., min_length=1, max_length=40), to_node_id: str = Query(..., min_length=1, max_length=40)):
        return service.routes(str(game_id), access_token, unit_type, from_node_id, to_node_id)

    @app.post("/api/v1/games/{game_id}/preparation/preview")
    def preparation_preview(game_id: UUID, body: PreparationPreview, access_token: token):
        return service.preview_preparation(str(game_id), access_token, body.context.model_dump(mode="json"),
                                           body.preparation.model_dump(mode="json", exclude_none=True))

    @app.get("/api/v1/games/{game_id}/proposals/{proposal_id}")
    def proposal(game_id: UUID, proposal_id: UUID, access_token: token):
        return service.proposal(str(game_id), proposal_id, access_token)

    @app.post("/api/v1/games/{game_id}/conditions/preview")
    def preview(game_id: UUID, body: ConditionPreview, access_token: token):
        return service.preview_condition(str(game_id), access_token, body.context.model_dump(mode="json"), body.condition.model_dump(mode="json"))

    @app.post("/api/v1/games/{game_id}/commands")
    def command(game_id: UUID, body: CommandRequest, idempotency_key: request_key, access_token: token):
        result, status = service.command(str(game_id), access_token, idempotency_key, body)
        return JSONResponse(status_code=status, content=jsonable_encoder(result))

    @app.get("/api/v1/games/{game_id}/commands/by-request")
    def receipt_by_request(game_id: UUID, origin_branch_id: UUID, request_key: UUID, access_token: token):
        return service.receipt(str(game_id), access_token, origin_branch_id=origin_branch_id, request_key=request_key)

    @app.get("/api/v1/games/{game_id}/commands/{command_id}")
    def receipt(game_id: UUID, command_id: UUID, access_token: token):
        return service.receipt(str(game_id), access_token, command_id=command_id)

    @app.get("/api/v1/games/{game_id}/saves")
    def saves(game_id: UUID, access_token: token, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        return service.saves(str(game_id), access_token, offset, limit)

    @app.get("/api/v1/games/{game_id}/events")
    def events(game_id: UUID, access_token: token, limit: int = Query(50, ge=1, le=100), cursor: str | None = Query(None, max_length=4096)):
        return service.events(str(game_id), access_token, limit, cursor)

    @app.get("/api/v1/games/{game_id}/replay")
    def replay(game_id: UUID, access_token: token, snapshot_id: UUID | None = None, branch_id: UUID | None = None,
               simulation_step: int | None = Query(None, ge=0, le=60)):
        return service.replay(str(game_id), access_token, snapshot_id=snapshot_id, branch_id=branch_id, simulation_step=simulation_step)

    @app.get("/api/v1/games/{game_id}/archives")
    def archives(game_id: UUID, access_token: token, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
                 search: str = Query("", max_length=80), phase: str = Query("all", pattern="^(all|active|finished)$")):
        return service.archives(str(game_id), access_token, offset=offset, limit=limit, search=search, phase=phase)

    @app.get("/api/v1/games/{game_id}/archives/{archive_id}")
    def archive(game_id: UUID, archive_id: UUID, access_token: token):
        return service.archives(str(game_id), access_token, archive_id=archive_id)

    @app.get("/api/v1/games/{game_id}/result")
    def result(game_id: UUID, access_token: token):
        return service.result(str(game_id), access_token)

    return app


def main() -> None:
    """显式初始化后启动 HTTP；不安装依赖或自动创建业务对局。"""
    parser = argparse.ArgumentParser(description="启动现有功能演示后端")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--initialize", action="store_true", help="显式登记当前演示版本及本机签名配置")
    parser.add_argument("--no-scheduler", action="store_true")
    arguments = parser.parse_args()
    if arguments.initialize:
        initialize_runtime()
    settings = Settings.from_local()
    if arguments.no_scheduler:
        from dataclasses import replace
        settings = replace(settings, scheduler_enabled=False)
    if arguments.initialize:
        SandboxService(settings).seed_versions()
    uvicorn.run(create_app(settings), host="127.0.0.1", port=arguments.port, access_log=False)


if __name__ == "__main__":
    main()
