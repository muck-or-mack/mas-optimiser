"""Start and stop a self-hosted model's server: the ``server`` command.

A model file with a ``server`` block (``configs/models/qwen3.6-27b.yaml``)
names a release of vLLM and the options its server starts with.
``mas-optimiser server <model>`` installs that release into an environment of
its own, the first time, and starts the server as a background process
listening where the file's ``llm_api_endpoint`` says; run again, it reports
whether the server is still loading or ready. ``--stop`` ends it. With
``--docker`` the same release runs as a container of vLLM's image instead.
Either way the server is reachable from this machine only, and the weights it
downloads are kept for the next start.

Sources: ``docs/how-it-works.md``, section 6, the row for ``server``.
"""

import argparse
import os
import shlex
import shutil
import signal
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml

from mas_optimiser.benchmarks.enterpriseops import runner

DESCRIPTION = "start or stop a self-hosted model's server"
# Where the environments of vLLM are built, one per release, and where each
# server's log and process id are kept.
STATE = Path.home() / ".cache" / "mas-optimiser"
# With --docker: the volume that keeps downloaded weights between containers,
# where the image keeps its Hugging Face cache, and the port its server
# listens on inside the container.
VOLUME = "mas-optimiser-models"
CONTAINER_CACHE = "/root/.cache/huggingface"
CONTAINER_PORT = 8000


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Define the command's options.

    Args:
        parser: The command's subparser.
    """
    parser.add_argument("model", help="model name in configs/models/, or a YAML path")
    parser.add_argument(
        "--stop",
        action="store_true",
        help="end the model's server instead of starting it",
    )
    parser.add_argument(
        "--docker",
        action="store_true",
        help="run the server as a container of vLLM's image, not as a process "
        "of this machine",
    )
    parser.add_argument(
        "--cache",
        help="the folder downloaded weights are kept in (default: the user's "
        "Hugging Face cache; with --docker, a volume's name or a folder's "
        f"absolute path, default the volume {VOLUME})",
    )


def _process(name: str) -> int | None:
    """The process id of a model's running server.

    The id is the one written at the start. It counts only while the process
    of that id is still a vLLM server, so that an id the system has since
    given to another program is not taken for it.

    Args:
        name: The model file's name, without its extension.

    Returns:
        The process id, or ``None`` if no server of the model is running.
    """
    try:
        pid = int((STATE / f"{name}.pid").read_text())
        command = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, ValueError):
        return None
    return pid if b"vllm" in command else None


def run(args: argparse.Namespace) -> None:
    """Start the model's server and report its state, or end it with ``--stop``.

    Starting is safe to repeat: a server that is running is left alone, and
    the command says whether it answers yet. A server that stopped by itself
    is not started again until ``--stop`` has cleared it, so that the reason
    can be read first. With ``--docker``, a start that fails removes the
    container it made.

    Args:
        args: The parsed options, as ``add_arguments`` defines them.

    Raises:
        SystemExit: If the model file has no ``server`` block, uv or Docker
            is not installed, a server of the model stopped by itself and has
            not been cleared, or a command that installs or starts the server
            fails (with that command's exit code).
    """
    path = runner.config_path("models", args.model)
    fields = yaml.safe_load(path.read_text())
    if "server" not in fields:
        raise SystemExit(
            f"error: {path} has no server block, so it names no server to start"
        )
    name = path.stem
    version = fields["server"]["vllm"]
    endpoint = fields["llm_api_endpoint"]
    port = urlsplit(endpoint).port
    options = [fields["llm_model"], *fields["server"]["args"]]
    clear = f"mas-optimiser server {args.model} --stop"

    if args.docker:
        if shutil.which("docker") is None:
            raise SystemExit("error: docker not found")
        if args.stop:
            stopped = subprocess.run(["docker", "rm", "--force", name])
            if stopped.returncode:
                raise SystemExit(stopped.returncode)
            return
        inspected = subprocess.run(
            ["docker", "container", "inspect", "--format", "{{.State.Status}}", name],
            capture_output=True,
            text=True,
        )
        status = inspected.stdout.strip()
        if inspected.returncode == 0 and status != "running":
            raise SystemExit(
                f"error: the container {name} exists and is {status}, not "
                f"running. Read why with: docker logs {name}\n"
                f"Then remove it with: {clear} --docker"
            )
        if inspected.returncode != 0:
            command = [
                "docker",
                "run",
                "--detach",
                f"--name={name}",
                "--gpus=all",
                # The server shares tensors between its processes through
                # the host's shared memory.
                "--ipc=host",
                f"--publish=127.0.0.1:{port}:{CONTAINER_PORT}",
                f"--volume={args.cache or VOLUME}:{CONTAINER_CACHE}",
                f"vllm/vllm-openai:v{version}",
                *options,
            ]
            print(shlex.join(command), flush=True)
            started = subprocess.run(command)
            if started.returncode:
                subprocess.run(["docker", "rm", "--force", name], capture_output=True)
                raise SystemExit(started.returncode)
        watch = f"docker logs -f {name}"
    else:
        pid_file, log = STATE / f"{name}.pid", STATE / f"{name}.log"
        pid = _process(name)
        if args.stop:
            if pid:
                # The server is the leader of its own process group, which
                # holds its workers too.
                os.killpg(pid, signal.SIGTERM)
                for _ in range(60):
                    if _process(name) is None:
                        break
                    time.sleep(0.5)
                else:
                    os.killpg(pid, signal.SIGKILL)
            pid_file.unlink(missing_ok=True)
            return
        if pid is None and pid_file.exists():
            raise SystemExit(
                f"error: the server of {name} stopped by itself. Read why in "
                f"{log}\nThen clear it with: {clear}"
            )
        if pid is None:
            # A uv installed a moment ago by setup.sh is not yet on PATH.
            uv = shutil.which("uv") or shutil.which(
                str(Path.home() / ".local" / "bin" / "uv")
            )
            if uv is None:
                raise SystemExit("error: uv not found; ./setup.sh installs it")
            environment = STATE / f"vllm-{version}"
            vllm = str(environment / "bin" / "vllm")
            # Written after the last step, so that an install cut short is
            # taken up again.
            installed = environment / "installed"
            if not installed.exists():
                # vLLM's own three steps for an install with uv. The second
                # picks the build of PyTorch that suits the NVIDIA driver;
                # the third fetches kernels the server would otherwise have
                # to compile, and looks for uv on PATH.
                path = f"{Path(uv).parent}{os.pathsep}{os.environ['PATH']}"
                environment.mkdir(parents=True, exist_ok=True)
                for command in (
                    [
                        uv,
                        "venv",
                        str(environment),
                        "--python=3.12",
                        "--seed",
                        "--managed-python",
                        "--allow-existing",
                    ],
                    [
                        uv,
                        "pip",
                        "install",
                        f"--python={environment}/bin/python",
                        f"vllm=={version}",
                        "--torch-backend=auto",
                    ],
                    [vllm, "download-kernels"],
                ):
                    print(shlex.join(command), flush=True)
                    built = subprocess.run(command, env=os.environ | {"PATH": path})
                    if built.returncode:
                        raise SystemExit(built.returncode)
                installed.touch()
            command = [vllm, "serve", *options]
            command += ["--host=127.0.0.1", f"--port={port}"]
            print(shlex.join(command), flush=True)
            STATE.mkdir(parents=True, exist_ok=True)
            with log.open("w") as output:
                started = subprocess.Popen(
                    command,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    # Its own session, so that it outlives this terminal.
                    start_new_session=True,
                    env=os.environ | ({"HF_HOME": args.cache} if args.cache else {}),
                )
            pid_file.write_text(str(started.pid))
        watch = f"tail -f {log}"

    # Ready is taken to mean that the server answers: it opens its port once
    # the model is loaded.
    try:
        httpx.get(f"{endpoint}/models", timeout=5)
        print(f"{name}: ready at {endpoint}")
    except httpx.HTTPError:
        print(
            f"{name}: loading (the first start downloads the model's weights). "
            f"Watch it with: {watch}\n"
            "Run this command again to see whether it is ready."
        )
