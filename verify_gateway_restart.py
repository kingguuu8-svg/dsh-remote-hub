"""Verify reboot-like SSH loss, hung workers and recovery without model requests."""

import argparse
import asyncio
import os
import shlex
import signal
import subprocess
import sys

from gateway import load_targets
from ssh_fixture import SSHFixture
from workspace_gateway import WorkspaceGateway


async def wait_until(predicate, timeout=45):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.1)


async def verify(ssh):
    workspace = ssh.target_root / "restart workspace"
    workspace.mkdir()
    targets = load_targets(ssh.config)
    marker = ssh.directory / "bootstrap-started"
    python = ssh.directory / "slow-python"
    python.write_text("#!/bin/sh\nprintf STARTED\\n >> " + shlex.quote(str(marker))
                      + "\nsleep 3\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
    python.chmod(0o700)
    targets["laptop"]["python"] = str(python)
    gateway = WorkspaceGateway(targets, "laptop", str(workspace))
    stopped = set()
    operations = []

    async def worker(backend):
        result = await backend.session.call_tool("machine_info", {})
        return result.structuredContent["pid"]

    async def proof():
        backend = await asyncio.wait_for(gateway.ensure_connection(), 45)
        result = await backend.call_tool("bash", {"command": "pwd"})
        assert not result.isError and str(workspace) in result.content[0].text
        return backend

    try:
        first = asyncio.create_task(gateway.ensure_connection())
        await wait_until(marker.exists)
        peer = asyncio.create_task(gateway.ensure_connection())
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        assert await asyncio.wait_for(peer, 45)
        assert marker.read_text().count("STARTED") == 1
        print("PASS cancelling a health-check waiter preserves one shared SSH initialization for its peer", flush=True)
        backend = await proof()
        binding = gateway.binding.copy()
        job = asyncio.create_task(backend.call_tool("bash", {
            "command": "printf ONCE >> once.txt; sleep 120; printf DONE > finished.txt",
        }))
        operations.append(job)
        await wait_until(lambda: (workspace / "once.txt").exists())
        os.kill(await worker(backend), signal.SIGKILL)
        await wait_until(lambda: backend.state == "failed")
        recovered = await proof()
        assert recovered is not backend and gateway.binding == binding
        await asyncio.wait_for(asyncio.gather(job, return_exceptions=True), 5)
        assert (workspace / "once.txt").read_text() == "ONCE"
        assert not (workspace / "finished.txt").exists()
        print("PASS abrupt worker loss restores the same workspace without replaying its in-flight mutation", flush=True)

        # Rebooted machines may keep TCP open while the application stalls.
        pid = await worker(recovered)
        os.kill(pid, signal.SIGSTOP)
        stopped.add(pid)
        await wait_until(lambda: recovered.state == "failed")
        os.kill(pid, signal.SIGCONT)
        stopped.remove(pid)
        fresh = await proof()
        assert fresh is not recovered
        print("PASS a silent worker is detected within 45 seconds and replaced", flush=True)

        # Listener down: reconnection fails honestly, then works with the same
        # credentials once SSH returns. Use only this fixture's sshd.
        os.kill(await worker(fresh), signal.SIGKILL)
        await wait_until(lambda: fresh.state == "failed")
        ssh.process.terminate()
        ssh.process.wait(timeout=5)
        try:
            await gateway.ensure_connection()
        except RuntimeError:
            pass
        else:
            raise AssertionError("Offline SSH target reported connected")
        ssh.process = subprocess.Popen(
            ["/usr/sbin/sshd", "-D", "-e", "-f", str(ssh.directory / "sshd_config")],
            stdout=ssh.log, stderr=ssh.log,
        )
        await asyncio.sleep(.3)
        assert await proof()
        assert gateway.binding == binding
        print("PASS SSH downtime followed by restart needs no credential or workspace changes", flush=True)

        closing = gateway.backends["laptop"]
        pid = await worker(closing)
        os.kill(pid, signal.SIGSTOP)
        stopped.add(pid)
        await asyncio.sleep(6)  # Let the periodic protocol read enter its wait.
        await asyncio.wait_for(gateway.close(), 15)
        await asyncio.wait_for(gateway.close(), 2)
        try:
            await gateway.ensure_connection()
        except RuntimeError:
            pass
        else:
            raise AssertionError("A closed gateway started another connection")
        print("PASS closing a silent connection finishes within 15 seconds", flush=True)
    finally:
        for pid in stopped:
            try:
                os.kill(pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
        await gateway.close()
        await asyncio.gather(*operations, return_exceptions=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", default="node")
    parser.add_argument("--npm", default="npm")
    args = parser.parse_args()
    with SSHFixture(node=args.node, npm=args.npm) as fixture:
        asyncio.run(verify(fixture))
