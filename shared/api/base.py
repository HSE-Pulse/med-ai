"""FastAPI application factory and shared API primitives.

Extended for GDPR / EU AI Act / HSE compliance:

- ``/privacy-notice`` — GDPR Art. 13/14 transparency endpoint.
- ``security_check()`` — validates prod env (TLS, Fernet key, MongoDB auth).
- ``@require_role`` decorator — JWT-based RBAC for GDPR Art. 9 data.
- ``PrivacyNotice`` dataclass — passed by each service at ``create_app`` time.

Services must pass a ``privacy_notice`` and optional ``ai_act_info`` when
calling :func:`create_app` so the factory can register the right endpoints.
"""

from __future__ import annotations

import os
import time
import traceback
from dataclasses import asdict, dataclass, field
from functools import wraps
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class BaseResponse(BaseModel):
    """Standard envelope for all API responses."""

    status: str = "ok"
    data: Optional[Any] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Privacy / compliance metadata
# ---------------------------------------------------------------------------


@dataclass
class PrivacyNotice:
    """GDPR Art. 13/14 disclosure served at ``/privacy-notice``."""

    data_collected: List[str]
    legal_basis: str
    retention_period: str
    third_party_sharing: List[str] = field(default_factory=list)
    dpo_contact: str = "dpo@medai-platform.local"
    subject_rights: List[str] = field(
        default_factory=lambda: [
            "access (Art. 15)",
            "rectification (Art. 16)",
            "erasure (Art. 17)",
            "restriction (Art. 18)",
            "portability (Art. 20)",
            "objection (Art. 21)",
        ]
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AIActInfo:
    """EU AI Act Art. 13 transparency disclosure for high-risk ML services."""

    risk_level: str  # "high" | "limited" | "minimal"
    intended_purpose: str
    known_limitations: List[str]
    training_data_description: str
    validation_metrics: Dict[str, Any]
    human_oversight_required: bool = True
    last_audit_date: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Startup security validation
# ---------------------------------------------------------------------------


class SecurityCheckError(RuntimeError):
    """Raised when production-mode configuration fails security checks."""


def security_check(mode: Optional[str] = None) -> Dict[str, Any]:
    """Validate environment for the current deployment mode.

    In ``production`` mode the following must all be set:
      - ``TLS_CERT_PATH`` + ``TLS_KEY_PATH`` (mTLS between services)
      - ``FERNET_KEY`` (field-level encryption for ICD / diagnosis)
      - ``MONGO_URI`` with ``authSource`` parameter

    In ``simulation`` (default) mode, warnings are produced but startup
    continues — the platform runs on synthetic MIMIC data with no real PHI.

    Returns a dict describing the check outcome for service dashboards.
    """
    effective_mode = (mode or os.environ.get("DEPLOYMENT_MODE", "simulation")).lower()
    issues: List[str] = []

    tls_cert = os.environ.get("TLS_CERT_PATH")
    tls_key = os.environ.get("TLS_KEY_PATH")
    if effective_mode == "production":
        if not (tls_cert and tls_key):
            issues.append("TLS_CERT_PATH/TLS_KEY_PATH missing — mTLS required in production")
        if not os.environ.get("FERNET_KEY"):
            issues.append("FERNET_KEY missing — field encryption required in production")
        mongo_uri = os.environ.get("MONGO_URI", "")
        if mongo_uri and "authSource" not in mongo_uri:
            issues.append("MONGO_URI missing authSource parameter")

    result: Dict[str, Any] = {
        "mode": effective_mode,
        "passed": not issues,
        "issues": issues,
        "simulation": effective_mode != "production",
    }
    if effective_mode == "production" and issues:
        raise SecurityCheckError("; ".join(issues))
    return result


# ---------------------------------------------------------------------------
# RBAC decorator (JWT-backed)
# ---------------------------------------------------------------------------


def require_role(*roles: str) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Require that the caller's JWT carries one of ``roles``.

    In simulation mode (default), RBAC is advisory: a missing/empty
    ``Authorization`` header is allowed and defaults to the ``researcher`` role.
    In production mode it enforces strict rejection.

    Usage::

        @app.get("/patient/{hadm_id}/vitals")
        @require_role("clinician", "admin")
        async def vitals(hadm_id: str, request: Request): ...
    """
    allowed = set(roles)

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            request: Optional[Request] = kwargs.get("request")
            if request is None:
                for a in args:
                    if isinstance(a, Request):
                        request = a
                        break

            token_role = _extract_role_from_request(request)
            mode = os.environ.get("DEPLOYMENT_MODE", "simulation").lower()

            if token_role is None:
                if mode == "production":
                    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing auth")
                token_role = "researcher"  # simulation-mode default

            if token_role not in allowed:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="role not permitted")

            # Attach role to request.state for downstream pseudonymisation
            if request is not None:
                request.state.role = token_role
            return await func(*args, **kwargs)

        return wrapper

    return decorator


def _extract_role_from_request(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    token = auth.split(" ", 1)[1].strip()
    if not token:
        return None
    # Lightweight JWT decode without verification for simulation mode.
    # Production mode must swap this for a verified decode using public key or shared secret.
    try:
        import base64
        import json

        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("role") or payload.get("scope")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


_start_time: float = time.time()


def create_app(
    title: str = "Healthcare AI API",
    version: str = "0.1.0",
    description: str = "",
    allowed_origins: list[str] | None = None,
    *,
    privacy_notice: Optional[PrivacyNotice] = None,
    ai_act_info: Optional[AIActInfo] = None,
    enable_security_check: bool = True,
) -> FastAPI:
    """Create and configure a FastAPI application.

    Parameters
    ----------
    title / version / description:
        OpenAPI metadata.
    allowed_origins:
        CORS allowed origins. Defaults to ``["*"]`` for development.
    privacy_notice:
        GDPR Art. 13/14 disclosure. If provided, the factory registers
        ``GET /privacy-notice`` returning this structure.
    ai_act_info:
        EU AI Act Art. 13 transparency structure for high-risk ML services.
        If provided, the factory registers ``GET /ai-act-info``.
    enable_security_check:
        When True, call :func:`security_check` at app construction time.
    """
    global _start_time
    _start_time = time.time()

    security_status: Dict[str, Any] = {}
    if enable_security_check:
        try:
            security_status = security_check()
        except SecurityCheckError as exc:
            # Reraise — production startup must fail fast.
            raise
        except Exception:
            security_status = {"mode": "unknown", "passed": False, "issues": ["check_failed"]}

    app = FastAPI(title=title, version=version, description=description)

    # -- CORS ---------------------------------------------------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Request timing middleware ------------------------------------------
    @app.middleware("http")
    async def add_timing_header(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
        return response

    # -- Global error handler -----------------------------------------------
    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        tb = traceback.format_exc()
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "error": str(exc),
                "detail": tb,
            },
        )

    # -- Health endpoint ----------------------------------------------------
    @app.get("/health", response_model=BaseResponse, tags=["system"])
    async def health() -> BaseResponse:
        uptime_seconds = round(time.time() - _start_time, 2)
        return BaseResponse(
            status="ok",
            data={
                "uptime_seconds": uptime_seconds,
                "version": version,
                "security": security_status,
            },
        )

    # -- Privacy notice (GDPR Art. 13/14) -----------------------------------
    if privacy_notice is not None:
        notice_payload = privacy_notice.to_dict()
        notice_payload["service_title"] = title
        notice_payload["service_version"] = version

        @app.get("/privacy-notice", response_model=BaseResponse, tags=["compliance"])
        async def get_privacy_notice() -> BaseResponse:
            return BaseResponse(status="ok", data=notice_payload)

    # -- AI Act transparency (Art. 13) --------------------------------------
    if ai_act_info is not None:
        ai_act_payload = ai_act_info.to_dict()
        ai_act_payload["service_title"] = title
        ai_act_payload["service_version"] = version

        @app.get("/ai-act-info", response_model=BaseResponse, tags=["compliance"])
        async def get_ai_act_info() -> BaseResponse:
            return BaseResponse(status="ok", data=ai_act_payload)

    return app


__all__ = [
    "AIActInfo",
    "BaseResponse",
    "PrivacyNotice",
    "SecurityCheckError",
    "create_app",
    "require_role",
    "security_check",
]
