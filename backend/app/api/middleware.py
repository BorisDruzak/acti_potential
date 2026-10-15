import ipaddress
import json
import logging
import re
from uuid import uuid4

from app.api.errors import error_response

logger = logging.getLogger("acti.http")


def source_address(request):
    address = request.client.host if request.client else "unknown"
    try:
        peer = ipaddress.ip_address(address)
        trusted = any(
            peer in ipaddress.ip_network(cidr.strip())
            for cidr in request.app.state.settings.trusted_proxy_cidrs.split(",")
            if cidr.strip()
        )
        if trusted:
            address = str(ipaddress.ip_address(request.headers.get("X-Real-IP", address)))
    except ValueError:
        pass
    return address


def register_middleware(app):
    @app.middleware("http")
    async def observe(request, call_next):
        candidate = request.headers.get("X-Request-ID", "")
        request.state.request_id = (
            candidate if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", candidate) else uuid4().hex
        )
        try:
            response = await call_next(request)
        except Exception as error:
            logger.error(
                json.dumps(
                    {"request_id": request.state.request_id, "error_type": type(error).__name__}
                )
            )
            response = error_response(request, 500, "internal_error", "Внутренняя ошибка сервера")
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        route = request.scope.get("route")
        logger.info(
            json.dumps(
                {
                    "request_id": request.state.request_id,
                    "method": request.method,
                    "route": getattr(route, "path", "unmatched"),
                    "status": response.status_code,
                }
            )
        )
        return response
