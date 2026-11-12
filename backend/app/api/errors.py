"""One error shape everywhere: {"error": {"code", "message", "details", "request_id"}}."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.services.audit import request_id_var

log = logging.getLogger("sentinelflow.errors")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None, headers: dict | None = None):
        self.status, self.code, self.message, self.details, self.headers = status, code, message, details, headers


def not_found(what: str = "resource") -> ApiError:
    # Used for both "doesn't exist" and "exists in another tenant" so IDs can't be probed.
    return ApiError(404, "not_found", f"{what} not found")


def forbidden(message: str = "You do not have permission to perform this action") -> ApiError:
    return ApiError(403, "forbidden", message)


def _body(code: str, message: str, details=None) -> dict:
    return {"error": {"code": code, "message": message, "details": details, "request_id": request_id_var.get()}}


_STATUS_CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
                 409: "conflict", 413: "payload_too_large", 415: "unsupported_media_type", 429: "rate_limited"}


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, e: ApiError):
        return JSONResponse(_body(e.code, e.message, e.details), status_code=e.status, headers=e.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError):
        details = [{"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]} for err in e.errors()]
        return JSONResponse(_body("validation_error", "Request validation failed", details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        return JSONResponse(_body(_STATUS_CODES.get(e.status_code, "error"), str(e.detail)), status_code=e.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, e: Exception):
        log.exception("unhandled error")
        return JSONResponse(_body("internal_error", "An unexpected error occurred"), status_code=500)
