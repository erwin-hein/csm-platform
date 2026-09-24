from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.errors import ServiceError
from app.web import auth, pages
from app.web.csrf import OriginCheckMiddleware
from app.web.templating import is_htmx, render

app = FastAPI(title="Shearwater CSM Platform (PoC)", docs_url=None, redoc_url=None, openapi_url=None)

# Order matters: the origin check runs first (outermost), then the session cookie.
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, session_cookie="csm_session",
                   https_only=settings.session_https_only, same_site="lax", max_age=14 * 24 * 3600)
app.add_middleware(OriginCheckMiddleware)

app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent / "static"), name="static")
app.include_router(auth.router)
app.include_router(pages.router)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.exception_handler(auth.LoginRequired)
def login_required(request: Request, exc: auth.LoginRequired):
    if is_htmx(request):
        return HTMLResponse("", headers={"HX-Redirect": "/login"})
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(ServiceError)
def service_error(request: Request, exc: ServiceError):
    if is_htmx(request):
        # Show the message in the page's flash area instead of swapping the target.
        resp = render(request, "partials/flash.html", message=str(exc))
        resp.headers["HX-Retarget"] = "#flash"
        resp.headers["HX-Reswap"] = "innerHTML"
        return resp
    return render(request, "error.html", message=str(exc), status_code=exc.status_code)
