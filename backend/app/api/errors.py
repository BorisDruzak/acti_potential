from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.identity.policy import PermissionDenied
from app.identity.service import AuthenticationError


def fail(status: int, code: str, message: str):
    raise HTTPException(status, {"code": code, "message": message})


def error_response(request, status, code, message, details=None):
    return JSONResponse(
        {
            "error": {"code": code, "message": message, "details": details},
            "request_id": request.state.request_id,
        },
        status_code=status,
        headers={"X-Request-ID": request.state.request_id, "Cache-Control": "no-store"},
    )


def register_errors(app):
    @app.exception_handler(AuthenticationError)
    async def unauthorized(request, error):
        return error_response(request, 401, "unauthorized", "Не удалось выполнить вход")

    @app.exception_handler(PermissionDenied)
    async def forbidden(request, error):
        return error_response(request, 403, "forbidden", "Недостаточно прав")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, error):
        detail = error.detail if isinstance(error.detail, dict) else {}
        return error_response(
            request,
            error.status_code,
            detail.get("code", f"http_{error.status_code}"),
            detail.get("message", "Запрос не выполнен"),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        details = [{"field": list(item["loc"]), "type": item["type"]} for item in error.errors()]
        return error_response(request, 422, "validation_error", "Неверные данные запроса", details)
