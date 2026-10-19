from fastapi import Request


def get_worker(request: Request):
    worker = getattr(request.app.state, "worker", None)
    if worker is None:
        raise RuntimeError("run worker is not running")
    return worker
