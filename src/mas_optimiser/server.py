"""Start and stop a self-hosted model's server: the ``server`` command.

A model file with a ``server`` block (``configs/models/qwen3.6-27b.yaml``)
names the server's image and the options the server starts with.
``mas-optimiser server <model>`` starts that image in Docker as one container,
named after the model file and listening where the file's
``llm_api_endpoint`` says; run again, it reports whether the server is still
loading or ready. ``mas-optimiser server <model> --stop`` removes the
container. The weights the server downloads are kept outside the container,
so a later start downloads nothing.

Sources: ``docs/how-it-works.md``, section 6, the row for ``server``.
"""

import argparse
import shlex
import shutil
import subprocess
from urllib.parse import urlsplit

import httpx
import yaml

from mas_optimiser.benchmarks.enterpriseops import runner

DESCRIPTION = "start or stop a self-hosted model's server in Docker"
# The Docker volume that keeps downloaded weights between containers. A
# volume and not the user's own Hugging Face cache: the server writes as
# root, and a cache folder owned by root stops the user's own downloads.
VOLUME = "mas-optimiser-models"
# Where the image keeps its Hugging Face cache, and the port its server
# listens on inside the container.
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
        help="remove the model's container instead of starting it",
    )
    parser.add_argument(
        "--cache",
        default=VOLUME,
        help="where downloaded weights are kept: a Docker volume's name, or a "
        "folder's absolute path (default: the volume %(default)s)",
    )


def run(args: argparse.Namespace) -> None:
    """Start the model's server and report its state, or remove it with ``--stop``.

    Starting is safe to repeat: a server that is running is left alone, and
    the command says whether it answers yet. A container that exists and is
    not running is left too, so that its log can be read. A start that fails
    removes the container it made, so that the next start begins clean.

    Args:
        args: The parsed options, as ``add_arguments`` defines them.

    Raises:
        SystemExit: If Docker is not installed, the model file has no
            ``server`` block, a container of the model exists and is not
            running, or Docker reports an error (with Docker's exit code).
    """
    if shutil.which("docker") is None:
        raise SystemExit("error: docker not found; the server runs in Docker")
    path = runner.config_path("models", args.model)
    fields = yaml.safe_load(path.read_text())
    if "server" not in fields:
        raise SystemExit(
            f"error: {path} has no server block, so it names no server to start"
        )
    name = path.stem
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
            f"error: the container {name} exists and is {status}, not running. "
            f"Read why with: docker logs {name}\n"
            f"Then remove it with: mas-optimiser server {args.model} --stop"
        )
    endpoint = fields["llm_api_endpoint"]
    if inspected.returncode != 0:
        command = [
            "docker",
            "run",
            "--detach",
            f"--name={name}",
            "--gpus=all",
            # The server shares tensors between its processes through the
            # host's shared memory.
            "--ipc=host",
            # Reachable from this machine only: the server asks for no key.
            f"--publish=127.0.0.1:{urlsplit(endpoint).port}:{CONTAINER_PORT}",
            f"--volume={args.cache}:{CONTAINER_CACHE}",
            fields["server"]["image"],
            f"--model={fields['llm_model']}",
            *fields["server"]["args"],
        ]
        print(shlex.join(command), flush=True)
        started = subprocess.run(command)
        if started.returncode:
            subprocess.run(["docker", "rm", "--force", name], capture_output=True)
            raise SystemExit(started.returncode)

    # Ready is taken to mean that the server answers: it opens its port once
    # the model is loaded.
    try:
        httpx.get(f"{endpoint}/models", timeout=5)
        print(f"{name}: ready at {endpoint}")
    except httpx.HTTPError:
        print(
            f"{name}: loading (the first start downloads the image and the "
            f"weights). Watch it with: docker logs -f {name}\n"
            "Run this command again to see whether it is ready."
        )
