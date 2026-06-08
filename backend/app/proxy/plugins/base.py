"""Plugin base classes and protocols.

RequestTransform is the protocol that all request-only transform plugins conform to.
BasePlugin is kept as a deprecated compatibility shim.
"""

from app.proxy.interceptor import RequestTransform, TransformContext

# Re-export for convenience — plugins import from here.
__all__ = ["RequestTransform", "TransformContext", "BasePlugin"]


# Legacy compatibility — use RequestTransform for new plugins.
class BasePlugin:
    """Deprecated. Use RequestTransform for request-only transforms.

    Kept as a stub for any external code that references it.
    """

    name: str
