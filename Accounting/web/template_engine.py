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
templates = Jinja2Templates(directory=os.path.join(_HERE, "templates"))
