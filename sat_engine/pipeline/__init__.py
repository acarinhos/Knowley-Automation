"""Session pipeline: planner -> generator -> verify -> originality -> store."""
from .session import run_session

__all__ = ["run_session"]
