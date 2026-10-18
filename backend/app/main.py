import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .agent import AgentRunner
from .artifacts import ArtifactManager
from .config import get_settings
from .data_access import DataAccess
from .python_runtime import PythonRuntime
from .store import Store


settings = get_settings()
store = Store(settings.app_database_path)
data = DataAccess(settings.resolved_data_path)
artifacts = ArtifactManager(settings.artifacts_path, store)
python_runtime = PythonRuntime(settings.resolved_data_path)
agent = AgentRunner(settings, store, data, artifacts, python_runtime)
background_tasks: set[asyncio.Task[Any]] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.initialize()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatCreate(BaseModel):
    title: str = Field(default="New analysis", min_length=1, max_length=120)


class ChatUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)


@app.get("/api/health")
def health() -> dict[str, Any]:
    data_ready = settings.resolved_data_path.exists()
    return {
        "status": "ok" if data_ready else "degraded",
        "data_ready": data_ready,
        "data_path": str(settings.resolved_data_path),
        "mode": "demo" if settings.use_demo_agent else "live",
        "model": settings.openai_model,
    }


@app.get("/api/dataset/summary")
def dataset_summary() -> dict[str, Any]:
    try:
        return data.summary()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/chats")
def list_chats() -> list[dict[str, Any]]:
    return store.list_chats()


@app.post("/api/chats", status_code=201)
def create_chat(payload: ChatCreate) -> dict[str, Any]:
    return store.create_chat(payload.title)


@app.get("/api/chats/{chat_id}")
def get_chat(chat_id: str) -> dict[str, Any]:
    try:
        return store.get_chat(chat_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chat not found") from exc


@app.patch("/api/chats/{chat_id}")
def update_chat(chat_id: str, payload: ChatUpdate) -> dict[str, Any]:
    title = payload.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="Chat title cannot be empty")
    try:
        store.get_chat(chat_id)
        store.update_chat_title(chat_id, title)
        return store.get_chat(chat_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chat not found") from exc


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: str) -> dict[str, str]:
    try:
        deleted = store.delete_chat(chat_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chat not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Wait for the active analysis to finish before deleting this chat") from exc
    artifacts.delete_chat_files(deleted["run_ids"], deleted["experiment_ids"])
    return {"id": chat_id}


@app.post("/api/chats/{chat_id}/messages", status_code=202)
async def create_message(chat_id: str, payload: MessageCreate) -> dict[str, Any]:
    try:
        chat = store.get_chat(chat_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chat not found") from exc

    active_run = next((run for run in chat["runs"] if run["status"] in {"queued", "running"}), None)
    if active_run:
        raise HTTPException(status_code=409, detail="This chat already has an active analysis")

    user_message = store.add_message(chat_id, "user", payload.content.strip())
    model_name = "local-demo" if settings.use_demo_agent else settings.openai_model
    run = store.create_run(chat_id, user_message["id"], model_name)
    task = asyncio.create_task(asyncio.to_thread(agent.run, run["id"]))
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    return {"message": user_message, "run": run}


@app.get("/api/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    try:
        store.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc

    last_event_header = request.headers.get("last-event-id")
    cursor = max(after, int(last_event_header)) if last_event_header and last_event_header.isdigit() else after

    async def event_stream():
        nonlocal cursor
        last_heartbeat = time.monotonic()
        while True:
            if await request.is_disconnected():
                return
            events = await asyncio.to_thread(store.events_after, run_id, cursor)
            for event in events:
                cursor = event["id"]
                payload = json.dumps(event["payload"], default=str)
                yield f"id: {cursor}\nevent: {event['event_type']}\ndata: {payload}\n\n"
            run = await asyncio.to_thread(store.get_run, run_id)
            if run["status"] in {"completed", "failed"} and not events:
                return
            if time.monotonic() - last_heartbeat > 12:
                yield ": heartbeat\n\n"
                last_heartbeat = time.monotonic()
            await asyncio.sleep(0.2)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/artifacts/{artifact_id}/download")
def download_artifact(artifact_id: str) -> FileResponse:
    try:
        artifact = store.get_artifact(artifact_id)
        path = artifacts.resolve(artifact["relative_path"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="Artifact file is missing")
    disposition = "inline" if artifact["kind"] == "figure" else "attachment"
    return FileResponse(
        path,
        media_type=artifact["mime_type"],
        filename=artifact["name"] if disposition == "attachment" else None,
        content_disposition_type=disposition,
    )


@app.get("/api/experiments")
def list_experiments() -> list[dict[str, Any]]:
    return store.list_experiments()


@app.post("/api/chats/{chat_id}/experiments", status_code=201)
def save_experiment(chat_id: str, payload: ExperimentCreate) -> dict[str, Any]:
    try:
        store.get_chat(chat_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chat not found") from exc
    return store.save_experiment(chat_id, payload.name, payload.description)


@app.get("/api/experiments/{experiment_id}/export")
def export_experiment(experiment_id: str) -> FileResponse:
    try:
        experiment = store.get_experiment(experiment_id)
        chat = store.get_chat(experiment["chat_id"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Experiment not found") from exc
    export_path = artifacts.build_experiment_export(experiment, chat)
    return FileResponse(
        export_path,
        media_type="application/zip",
        filename=f"{experiment['name'].replace(' ', '-').lower()}.zip",
    )
