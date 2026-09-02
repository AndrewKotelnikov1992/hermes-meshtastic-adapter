try:
    from .adapter import register
except ImportError:  # Direct file loading (pytest / simple plugin loaders).
    from adapter import register

__all__ = ["register"]
