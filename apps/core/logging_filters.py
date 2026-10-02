import logging

from apps.core.middleware import get_current_request_id
class RequestIDLogFilter(logging.Filter):
    """Injects the current request's correlation ID into log records."""
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_current_request_id()
    return True
