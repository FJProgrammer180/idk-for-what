"""Application error types, exception handlers and the response envelope."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError  # noqa: F401  (re-exported for tools)
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, request_id_var
from app.numerical.common import NumericalInputError
from app.services.expr import ExpressionError

logger = get_logger("physicslab.api")


class AppError(Exception):
    """Base class for handled application errors (never leaks internals)."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "bad_request"

    def __init__(self, message: str, *, details: Any = None, status_code: int | None = None,
                 code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if status_code:
            self.status_code = status_code
        if code:
            self.code = code

    def to_envelope(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
                "request_id": request_id_var.get(),
            }
        }


class ValidationError(AppError):
    """A well-formed request that the server's own policy refuses to satisfy."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "policy_validation_failed"


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class AuthenticationError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthenticated"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"


class PayloadTooLargeError(AppError):
    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    code = "payload_too_large"


class ServiceUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        if exc.status_code < 500:
            logger.info("handled error", extra={"event": exc.code, "status_code": exc.status_code})
        else:
            logger.error("service error", extra={"event": exc.code, "status_code": exc.status_code})
        return JSONResponse(status_code=exc.status_code, content=exc.to_envelope())

    @app.exception_handler(NumericalInputError)
    async def _numerical_error(_: Request, exc: NumericalInputError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {
                    "code": "invalid_numerical_input",
                    "message": str(exc),
                    "details": None,
                    "request_id": request_id_var.get(),
                }
            },
        )

    @app.exception_handler(ExpressionError)
    async def _expression_error_handler(_: Request, exc: ExpressionError) -> JSONResponse:
        # the restricted parser's own failures are input problems, never server faults
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {
                    "code": "invalid_expression",
                    "message": str(exc),
                    "details": None,
                    "request_id": request_id_var.get(),
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        safe = []
        for err in exc.errors():
            loc = [str(part) for part in err.get("loc", [])]
            # never echo submitted values back for password-ish fields
            if any(part in {"password", "current_password", "new_password"} for part in loc):
                safe.append({"loc": loc, "msg": "value did not meet the requirements"})
            else:
                safe.append(
                    {
                        "loc": loc,
                        "msg": str(err.get("msg", "invalid value"))[:300],
                        "type": str(err.get("type", "")),
                    }
                )
        # naming the offending fields in the message means a client that only reads
        # `error.message` still learns what to fix, not just that something was wrong
        summary = "; ".join(".".join(item["loc"][1:]) or "body" for item in safe[:4])
        message = f"The request is invalid: {summary}." if summary else "The request body or query parameters are invalid."
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {
                    "code": "request_validation_failed",
                    "message": message,
                    "details": safe,
                    "request_id": request_id_var.get(),
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "request failed"
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": f"http_{exc.status_code}",
                    "message": message,
                    "details": None,
                    "request_id": request_id_var.get(),
                }
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled server error", extra={"event": "unhandled_exception"})
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "internal_error",
                    "message": (
                        "The server failed to complete this request. The failure has been "
                        "logged with the request id."
                    ),
                    "details": None,
                    "request_id": request_id_var.get(),
                }
            },
        )
