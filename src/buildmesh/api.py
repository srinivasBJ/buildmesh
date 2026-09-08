from __future__ import annotations

from typing import Any, Literal
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .service import BuildMeshService
from .connectors import ConnectorError
from .ingestion import IngestionError
from .storage import NotFoundError


class ProjectIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    location: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskIn(BaseModel):
    title: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class ProjectMemberIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=160)
    email: str = Field(min_length=3, max_length=320)
    roles: list[str] = Field(min_length=1, max_length=12)


class TaskAssignmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_id: str = Field(min_length=2)
    assigned_by: str = Field(min_length=2, max_length=160)


class TaskDependencyIn(BaseModel):
    prerequisite_task_id: str


class TaskStatusIn(BaseModel):
    status: Literal["open", "in_progress", "blocked", "completed"]
    changed_by: str = Field(min_length=2, max_length=160)
    reason: str | None = Field(default=None, max_length=1000)


class ProgressIn(BaseModel):
    task_ref: str
    reported_percent: float = Field(ge=0, le=100)
    planned_quantity: float | None = Field(default=None, gt=0)
    completed_quantity: float | None = Field(default=None, ge=0)
    material_units: float | None = Field(default=None, ge=0)
    reporter: str = Field(min_length=2)


class WeatherIn(BaseModel):
    source: str
    rain_probability: float = Field(ge=0, le=1)
    hours_until: int = Field(ge=0, le=720)
    summary: str = ""


class ObservationIn(BaseModel):
    source: str
    model: str
    observations: list[dict[str, Any]]
    site_ref: str | None = Field(default=None, min_length=2, max_length=160)


class ReviewIn(BaseModel):
    reviewer: str
    decision: Literal["approved", "rejected"]
    comment: str | None = None


class WeatherRefreshIn(BaseModel):
    horizon_hours: int = Field(default=48, ge=1, le=168)


class BenchmarkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repetitions: int = Field(default=8, ge=3, le=100)


class QualcommResultIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    job_id: str
    target_device: str
    model: str
    runtime: str
    compute_unit: str
    latency_ms: float
    source_url: str
    completed_at: str


class NotificationIn(BaseModel):
    recipient: str = Field(min_length=3)


class ContextIn(BaseModel):
    kind: Literal["soil_condition", "cad_extract", "environment_context"]
    source: str = Field(min_length=2)
    payload: dict[str, Any]
    confidence: float | None = Field(default=None, ge=0, le=1)


class ScheduleIn(BaseModel):
    task_ref: str = Field(min_length=2)
    planned_percent: float = Field(ge=0, le=100)
    days_remaining: int = Field(ge=0, le=3650)
    source: str = Field(min_length=2)
    current_phase: str | None = Field(default=None, max_length=160)


class TrafficWindowIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    congestion_index: float = Field(ge=0, le=1)
    sample_count: int = Field(ge=1, le=1_000_000)


class TrafficIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=2, max_length=160)
    observed_at: str = Field(min_length=1, max_length=80)
    confidence: float = Field(ge=0, le=1)
    windows: list[TrafficWindowIn] = Field(min_length=2, max_length=48)


class PlanPrerequisiteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_document_evidence_id: str = Field(min_length=2)
    predecessor_task_ref: str = Field(min_length=2)
    dependent_task_ref: str = Field(min_length=2)
    evidence_quote: str = Field(min_length=3, max_length=1000)


class EdgeIn(BaseModel):
    source_id: str
    target_id: str
    relation: str = Field(min_length=2, max_length=80)
    attributes: dict[str, Any] = Field(default_factory=dict)


class QuestionIn(BaseModel):
    question: str = Field(min_length=2, max_length=2000)


def create_app(database: str = "buildmesh.db", asset_root: str | None = None) -> FastAPI:
    service = BuildMeshService(database, asset_root)
    app = FastAPI(title="BuildMesh API", version="0.1.0", description="Local-first construction intelligence with OpenMesh orchestration and human approval gates.")
    static_root = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static_root), name="static")

    @app.exception_handler(NotFoundError)
    async def missing_resource(_, exc: NotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(IngestionError)
    @app.exception_handler(ConnectorError)
    async def bad_connector_or_asset(_, exc: Exception):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "buildmesh"}

    @app.get("/edge/status")
    def edge_status() -> dict[str, Any]:
        return service.edge_status()

    @app.get("/edge/qnn-readiness")
    def qnn_readiness() -> dict[str, Any]:
        return service.qnn_readiness()

    @app.get("/", include_in_schema=False)
    def workspace() -> FileResponse:
        return FileResponse(static_root / "index.html")

    @app.get("/projects")
    def list_projects() -> list[dict[str, Any]]:
        return service.store.list_projects()

    @app.post("/projects", status_code=201)
    def create_project(payload: ProjectIn) -> dict[str, Any]:
        return service.create_project(payload.name, payload.location, payload.metadata)

    @app.get("/projects/{project_id}/graph")
    def graph(project_id: str) -> dict[str, Any]:
        return service.store.graph(project_id)

    @app.get("/projects/{project_id}/timeline")
    def timeline(project_id: str) -> list[dict[str, Any]]:
        return service.store.events(project_id)

    @app.get("/projects/{project_id}/recommendations")
    def recommendations(project_id: str) -> list[dict[str, Any]]:
        return service.store.recommendations(project_id)

    @app.get("/projects/{project_id}/evidence")
    def evidence(project_id: str) -> list[dict[str, Any]]:
        return service.store.evidence(project_id)

    @app.get("/projects/{project_id}/agent-runs")
    def agent_runs(project_id: str) -> list[dict[str, Any]]:
        return service.store.agent_runs(project_id)

    @app.get("/projects/{project_id}/members")
    def members(project_id: str) -> list[dict[str, Any]]:
        return [node for node in service.store.graph(project_id)["nodes"] if node["kind"] == "project_member"]

    @app.post("/projects/{project_id}/members", status_code=201)
    def add_member(project_id: str, payload: ProjectMemberIn) -> dict[str, Any]:
        try:
            return service.add_project_member(project_id, **payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/tasks", status_code=201)
    def create_task(project_id: str, payload: TaskIn) -> dict[str, Any]:
        return service.create_task(project_id, payload.title, payload.attributes)

    @app.post("/projects/{project_id}/tasks/{task_id}/dependencies", status_code=201)
    def add_task_dependency(project_id: str, task_id: str, payload: TaskDependencyIn) -> dict[str, Any]:
        try:
            return service.add_task_dependency(project_id, task_id, payload.prerequisite_task_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/tasks/{task_id}/status")
    def set_task_status(project_id: str, task_id: str, payload: TaskStatusIn) -> dict[str, Any]:
        try:
            return service.set_task_status(project_id, task_id, **payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/tasks/{task_id}/assign", status_code=201)
    def assign_task(project_id: str, task_id: str, payload: TaskAssignmentIn) -> dict[str, Any]:
        try:
            return service.assign_task(project_id, task_id, **payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/edges", status_code=201)
    def connect_nodes(project_id: str, payload: EdgeIn) -> dict[str, Any]:
        return service.connect(project_id, **payload.model_dump())

    @app.post("/projects/{project_id}/updates", status_code=201)
    def progress(project_id: str, payload: ProgressIn) -> dict[str, Any]:
        return service.progress_update(project_id, **payload.model_dump())

    @app.post("/projects/{project_id}/context/weather", status_code=201)
    def weather(project_id: str, payload: WeatherIn) -> dict[str, Any]:
        return service.weather_context(project_id, **payload.model_dump())

    @app.post("/projects/{project_id}/context", status_code=201)
    def context(project_id: str, payload: ContextIn) -> dict[str, Any]:
        try:
            return service.record_context(project_id, **payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/context/traffic", status_code=201)
    def traffic(project_id: str, payload: TrafficIn) -> dict[str, Any]:
        try:
            return service.traffic_context(project_id, **payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/context/schedule", status_code=201)
    def schedule(project_id: str, payload: ScheduleIn) -> dict[str, Any]:
        try:
            return service.schedule_context(project_id, **payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/plan-prerequisites", status_code=201)
    def plan_prerequisite(project_id: str, payload: PlanPrerequisiteIn) -> dict[str, Any]:
        try:
            return service.record_plan_prerequisite(project_id, **payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/context/weather/refresh", status_code=201)
    def refresh_weather(project_id: str, payload: WeatherRefreshIn) -> dict[str, Any]:
        try:
            return service.refresh_weather(project_id, payload.horizon_hours)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/observations", status_code=201)
    def observations(project_id: str, payload: ObservationIn) -> dict[str, Any]:
        try:
            return service.record_observations(project_id, **payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/assets/images", status_code=201)
    async def upload_image(project_id: str, file: UploadFile = File(...), site_ref: str | None = Form(default=None, min_length=2, max_length=160)) -> dict[str, Any]:
        if not file.filename:
            raise HTTPException(status_code=422, detail="uploaded image needs a filename")
        try:
            return service.ingest_image(project_id, file.file, file.filename, file.content_type or "application/octet-stream", site_ref)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/assets/images/{evidence_id}/analyze", status_code=201)
    def analyze_image(project_id: str, evidence_id: str) -> dict[str, Any]:
        try:
            return service.analyze_image(project_id, evidence_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/assets/images/{evidence_id}/benchmark", status_code=201)
    def benchmark_image(project_id: str, evidence_id: str, payload: BenchmarkIn) -> dict[str, Any]:
        try:
            return service.benchmark_image(project_id, evidence_id, payload.repetitions)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/qualcomm-results", status_code=201)
    def import_qualcomm_result(project_id: str, payload: QualcommResultIn) -> dict[str, Any]:
        try:
            return service.import_qualcomm_result(project_id, payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/projects/{project_id}/assets/documents", status_code=201)
    async def upload_document(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
        if not file.filename:
            raise HTTPException(status_code=422, detail="uploaded document needs a filename")
        return service.ingest_document(project_id, file.file, file.filename, file.content_type or "application/octet-stream")

    @app.post("/projects/{project_id}/orchestrate")
    def orchestrate(project_id: str) -> dict[str, Any]:
        return service.openmesh.run(project_id)

    @app.get("/projects/{project_id}/reports/daily")
    def daily_report(project_id: str) -> dict[str, Any]:
        return service.daily_report(project_id)

    @app.post("/projects/{project_id}/ask")
    def ask_project(project_id: str, payload: QuestionIn) -> dict[str, Any]:
        try:
            return service.ask_project(project_id, payload.question)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/recommendations/{recommendation_id}/approve")
    def approve(recommendation_id: str, payload: ReviewIn) -> dict[str, Any]:
        try:
            return service.approve(recommendation_id, **payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/recommendations/{recommendation_id}/reviewer-candidates")
    def reviewer_candidates(recommendation_id: str) -> list[dict[str, Any]]:
        return service.recommendation_reviewer_candidates(recommendation_id)

    @app.post("/recommendations/{recommendation_id}/notify-reviewer")
    def notify_reviewer(recommendation_id: str, payload: NotificationIn) -> dict[str, Any]:
        return service.notify_reviewer(recommendation_id, payload.recipient)

    return app
