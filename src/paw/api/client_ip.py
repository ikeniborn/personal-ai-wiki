from fastapi import Request


def client_ip(request: Request) -> str:
    """Return the ASGI client IP used for throttling.

    Deployments behind a reverse proxy must configure the ASGI server/proxy
    chain so ``request.client`` is the trusted client address.
    """
    return request.client.host if request.client else "unknown"
