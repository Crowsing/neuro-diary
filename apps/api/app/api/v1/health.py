"""Health route."""

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    """Liveness plus the one non-secret fact the client has to show.

    `app_env` is here because of blocker 1 of the phase 2 prompt: a stand that
    accepts unfrozen consent copy must say so visibly. Reading it from the
    server rather than from a build flag means the stand cannot forget to.
    """
    services = request.app.state.services
    return {"status": "ok", "app_env": services.app_env}
