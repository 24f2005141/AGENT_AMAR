import os
import socket

import uvicorn

if __name__ == "__main__":
    host = os.getenv("BACKEND_HOST", "::")
    port = int(os.getenv("BACKEND_PORT", "8000"))
    if host == "::" and socket.has_dualstack_ipv6():
        # A preconfigured dual-stack socket is required on Windows. Uvicorn's
        # normal `host="::"` socket is IPv6-only there, which lets the tablet
        # connect over an IPv6-only phone hotspot but breaks ADB/Chrome's IPv4
        # localhost OAuth callback. One socket keeps one scheduler/app instance.
        listener = socket.create_server(
            (host, port),
            family=socket.AF_INET6,
            dualstack_ipv6=True,
        )
        config = uvicorn.Config("app.main:app", log_level="info")
        uvicorn.Server(config).run(sockets=[listener])
    else:
        uvicorn.run("app.main:app", host=host, port=port)
