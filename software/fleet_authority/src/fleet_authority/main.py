import uvicorn

from .app import create_app


app = create_app()


def run() -> None:
    uvicorn.run("fleet_authority.main:app", host="127.0.0.1", port=8091, proxy_headers=False)
