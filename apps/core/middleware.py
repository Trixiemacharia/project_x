import contextvars
import uuid

contextvars (not threading.local) so this stays correct under ASGI/async
views and Channels consumers, not just sync WSGI threads.
_request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
class RequestIDMiddleware:
    """Attaches a correlation ID to every request: read from an incoming
    X-Request-ID header if present (so a caller/gateway can supply one),
    otherwise generated. Stored on the request and echoed back in the
    response header so client and server logs can be joined."""
    header_name = "X-Request-ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.META.get("HTTP_X_REQUEST_ID") or str(uuid.uuid4())
        request.request_id = request_id
        token = _request_id_ctx.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            _request_id_ctx.reset(token)
        response[self.header_name] = request_id
        return response

def get_current_request_id() -> str:
    return _request_id_ctx.get()
