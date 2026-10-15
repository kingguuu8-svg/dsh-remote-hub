"""Bound cancellation delivery when a disconnected MCP writer stops consuming."""

import asyncio
from datetime import timedelta

import anyio
from mcp import types
from mcp.shared.exceptions import McpError
from mcp.shared.message import SessionMessage

from gateway import CancellationClientSession


async def verify_case(*, timeout=False, consume=True):
    incoming_writer, incoming = anyio.create_memory_object_stream(0)
    outgoing, outgoing_reader = anyio.create_memory_object_stream(0)
    async with incoming_writer, outgoing_reader, CancellationClientSession(
        incoming, outgoing,
        read_timeout_seconds=timedelta(milliseconds=50) if timeout else None,
    ) as session:
        request = asyncio.create_task(session.list_tools())
        initial = await outgoing_reader.receive()
        if not timeout:
            request.cancel()
        if consume:
            notification = await asyncio.wait_for(outgoing_reader.receive(), 2)
            assert notification.message.root.method == "notifications/cancelled"
            assert notification.message.root.params["requestId"] == initial.message.root.id
        # Leave the receive stream open but unread, as when the SSH stdio
        # writer has exited and its parent is waiting for cancellation cleanup.
        done, _ = await asyncio.wait({request}, timeout=2)
        if not done:
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
            raise AssertionError("Cancellation delivery blocked transport cleanup")
        if timeout:
            try:
                request.result()
            except McpError as error:
                assert error.error.code == 408
            else:
                raise AssertionError("The original timeout was lost")
        else:
            assert request.cancelled(), "The original cancellation was lost"

        # A per-request delivery timeout must not close a usable shared session.
        following = asyncio.create_task(session.list_tools())
        message = await outgoing_reader.receive()
        await incoming_writer.send(SessionMessage(types.JSONRPCMessage(
            types.JSONRPCResponse(jsonrpc="2.0", id=message.message.root.id,
                                  result={"tools": []})
        )))
        assert (await asyncio.wait_for(following, 2)).tools == []


async def verify():
    for timeout in (False, True):
        for consume in (True, False):
            await verify_case(timeout=timeout, consume=consume)
            print(f"PASS timeout={timeout}, consuming writer={consume}: original error preserved; shared session survives", flush=True)


if __name__ == "__main__":
    asyncio.run(verify())
