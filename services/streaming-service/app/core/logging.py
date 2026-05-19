"""JSON logging setup for Streaming Service."""
import logging
import logging.config
from pythonjsonlogger import jsonlogger
from contextvars import ContextVar
from typing import Optional

correlation_id: ContextVar[Optional[str]] = ContextVar('correlation_id', default=None)
request_id: ContextVar[Optional[str]] = ContextVar('request_id', default=None)


class ContextFilter(logging.Filter):
    """Add correlation and request IDs to log records."""
    
    def filter(self, record):
        record.correlation_id = get_correlation_id()
        record.request_id = get_request_id()
        return True


def setup_logging():
    """Configure JSON logging."""
    logging.config.dictConfig({
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'json': {
                '()': jsonlogger.JsonFormatter,
                'format': '%(asctime)s %(name)s %(levelname)s %(message)s %(correlation_id)s %(request_id)s'
            }
        },
        'filters': {
            'context_filter': {'()': ContextFilter}
        },
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'level': 'DEBUG',
                'formatter': 'json',
                'filters': ['context_filter']
            }
        },
        'loggers': {
            '': {
                'handlers': ['console'],
                'level': 'DEBUG',
                'propagate': True
            }
        }
    })


def set_correlation_id(cid: str):
    correlation_id.set(cid)


def set_request_id(rid: str):
    request_id.set(rid)


def get_correlation_id() -> Optional[str]:
    return correlation_id.get()


def get_request_id() -> Optional[str]:
    return request_id.get()
