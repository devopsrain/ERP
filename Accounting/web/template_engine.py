"""
Shared Jinja2Templates instance for FastAPI routes.

Import `templates` here to avoid circular imports.
Route handlers call:
    ctx = template_context(request)
    ctx.update(my_key=my_val)
    return templates.TemplateResponse("path/file.html", ctx)
"""
import os
from fastapi.templating import Jinja2Templates

_HERE = os.path.dirname(os.path.abspath(__file__))


class _CompatTemplates(Jinja2Templates):
    """Starlette 0.47 removed the legacy TemplateResponse(name, context)
    calling convention this codebase uses everywhere. Translate old-style
    calls to the new (request, name, context) signature; the request is
    taken from the context dict, which template_context() always sets."""

    def TemplateResponse(self, *args, **kwargs):  # noqa: N802 — Starlette API name
        if args and isinstance(args[0], str):
            name = args[0]
            context = args[1] if len(args) > 1 else (kwargs.pop("context", None) or {})
            request = context.get("request")
            return super().TemplateResponse(request, name, context, *args[2:], **kwargs)
        return super().TemplateResponse(*args, **kwargs)


templates = _CompatTemplates(directory=os.path.join(_HERE, "templates"))
