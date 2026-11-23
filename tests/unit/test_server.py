"""Unit tests of the ``server`` command, with Docker and the server stood in for."""

from types import SimpleNamespace

import httpx
import pytest
import yaml

from mas_optimiser import cli, server
from mas_optimiser.benchmarks.enterpriseops import runner

MODEL = """\
llm_provider: vllm
llm_model: org/model
llm_api_endpoint: http://localhost:8123/v1
server:
  image: vllm/vllm-openai:v1
  args:
    - --tensor-parallel-size=2
    - --enable-auto-tool-choice
"""
RUN = [
    "docker",
    "run",
    "--detach",
    "--name=model",
    "--gpus=all",
    "--ipc=host",
    "--publish=127.0.0.1:8123:8000",
    "--volume=mas-optimiser-models:/root/.cache/huggingface",
    "vllm/vllm-openai:v1",
    "--model=org/model",
    "--tensor-parallel-size=2",
    "--enable-auto-tool-choice",
]
INSPECT = ["docker", "container", "inspect", "--format", "{{.State.Status}}", "model"]
REMOVE = ["docker", "rm", "--force", "model"]


@pytest.fixture
def docker(tmp_path, monkeypatch):
    """A model file, and Docker replaced by a record of the commands it is sent.

    ``answers`` maps a command's second word to the exit code and output it
    gets; a command not named succeeds with no output. The server does not
    answer unless a test says so.
    """
    path = tmp_path / "model.yaml"
    path.write_text(MODEL)
    stand_in = SimpleNamespace(path=str(path), calls=[], answers={})

    def run(command, **_):
        stand_in.calls.append(command)
        code, out = stand_in.answers.get(command[1], (0, ""))
        return SimpleNamespace(returncode=code, stdout=out)

    def no_answer(url, **_):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(server.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(server.subprocess, "run", run)
    monkeypatch.setattr(server.httpx, "get", no_answer)
    return stand_in


def test_server_starts_the_image_with_the_options_of_the_model_file(docker, capsys):
    docker.answers["container"] = (1, "")
    cli.main(["server", docker.path])
    assert docker.calls == [INSPECT, RUN]
    out = capsys.readouterr().out
    assert "--publish=127.0.0.1:8123:8000" in out
    assert "model: loading" in out and "docker logs -f model" in out


def test_server_keeps_the_weights_where_the_cache_option_says(docker):
    docker.answers["container"] = (1, "")
    cli.main(["server", docker.path, "--cache", "/data/weights"])
    assert "--volume=/data/weights:/root/.cache/huggingface" in docker.calls[1]


def test_server_leaves_a_running_server_alone_and_says_when_it_answers(
    docker, monkeypatch, capsys
):
    docker.answers["container"] = (0, "running\n")
    asked = []
    monkeypatch.setattr(server.httpx, "get", lambda url, **_: asked.append(url))
    cli.main(["server", docker.path])
    assert docker.calls == [INSPECT]
    assert asked == ["http://localhost:8123/v1/models"]
    assert "model: ready at http://localhost:8123/v1" in capsys.readouterr().out


def test_server_keeps_a_stopped_container_for_its_log(docker):
    docker.answers["container"] = (0, "exited\n")
    with pytest.raises(SystemExit, match="is exited.*docker logs model"):
        cli.main(["server", docker.path])
    assert docker.calls == [INSPECT]


def test_a_start_that_fails_removes_its_container(docker):
    docker.answers.update(container=(1, ""), run=(125, ""))
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["server", docker.path])
    assert exit_info.value.code == 125
    assert docker.calls == [INSPECT, RUN, REMOVE]


def test_stop_removes_the_container(docker):
    cli.main(["server", docker.path, "--stop"])
    assert docker.calls == [REMOVE]


def test_server_refuses_a_model_file_without_a_server_block(docker):
    with pytest.raises(SystemExit, match="no server block"):
        cli.main(["server", str(runner.config_path("models", "haiku-5-5"))])
    assert docker.calls == []


def test_server_needs_docker(docker, monkeypatch):
    monkeypatch.setattr(server.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="docker not found"):
        cli.main(["server", docker.path])


def test_the_qwen_file_names_a_released_image_and_one_option_an_item():
    block = yaml.safe_load(runner.config_path("models", "qwen3.6-27b").read_text())
    assert block["server"]["image"].startswith("vllm/vllm-openai:v")
    assert all(a.startswith("--") and " " not in a for a in block["server"]["args"])
