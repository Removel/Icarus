"""One lifecycle owner runs local and optional service routing shells in this process."""

import argparse
import asyncio
import ipaddress
import os

import uvicorn

from apps.gateway.src.app import create_app


def private_address(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not address.is_unspecified and (address.is_loopback or address.is_private or address in ipaddress.ip_network('100.64.0.0/10'))


async def serve_pair(owner, service, *, host, port, service_host, service_port):
    async with owner.router.lifespan_context(owner):
        servers = [uvicorn.Server(uvicorn.Config(owner, host=host, port=port, lifespan='off')),
                   uvicorn.Server(uvicorn.Config(service, host=service_host, port=service_port, lifespan='off'))]
        tasks = [asyncio.create_task(server.serve()) for server in servers]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for server in servers:
                server.should_exit = True
            await asyncio.gather(*tasks, return_exceptions=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='Icarus Agent Gateway')
    parser.add_argument('--host', choices=('127.0.0.1', '::1'), default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--service-host')
    parser.add_argument('--service-port', type=int)
    args = parser.parse_args(argv)
    if (args.service_host is None) != (args.service_port is None):
        parser.error('Service host and port must be supplied together')
    if args.service_host is not None:
        if not private_address(args.service_host) or not 1 <= args.service_port <= 65535:
            parser.error('Service listener requires a private LAN/VPN address and valid port')
        if not os.getenv('ARGUS_GATEWAY_TOKEN'):
            parser.error('ARGUS_GATEWAY_TOKEN is required for service listener')
    owner = create_app()
    if args.service_host is None:
        uvicorn.run(owner, host=args.host, port=args.port)
    else:
        service = create_app(service_only=True, shared_app=owner)
        asyncio.run(serve_pair(owner, service, host=args.host, port=args.port,
                               service_host=args.service_host, service_port=args.service_port))


if __name__ == '__main__':
    main()
