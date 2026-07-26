"""Health route."""

from fastapi import APIRouter, Request

from app.api.v1.responses import refusals

router = APIRouter()


@router.get(
    "/health",
    # 422 is here because `QueryParameterAllowlistMiddleware` can answer it on
    # **any** path, including this one, and a status the code can produce has to
    # be declared — that is the whole point of `responses.py`. Found by the
    # independent review of Phase 5: this route was the single operation with no
    # `authorization` header parameter, so FastAPI added no 422 of its own, and
    # `/health?x=1` answered an undeclared 422.
    responses=refusals(),
)
def health(request: Request) -> dict[str, str]:
    """Liveness plus the one non-secret fact the client has to show.

    `app_env` is here because of blocker 1 of the phase 2 prompt: a stand that
    accepts unfrozen consent copy must say so visibly. Reading it from the
    server rather than from a build flag means the stand cannot forget to.
    """
    services = request.app.state.services
    return {"status": "ok", "app_env": services.app_env}
