"""Unit tests of the ``server`` command, with the machine's commands stood in for."""

import signal
import subprocess
import sys
from pathlib import Path
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
  vllm: "1"
  args:
    - --tensor-parallel-size=2
    - --enable-auto-tool-choice
"""
OPTIONS = ["org/model", "--tensor-parallel-size=2", "--enable-auto-tool-choice"]
DOCKER_RUN = [
    "docker",
    "run",
    "--detach",
    "--name=model",
    "--gpus=all",
    "--ipc=host",
    "--publish=127.0.0.1:8123:8000",
    "--volume=mas-optimiser-models:/root/.cache/huggingface",
    "vllm/vllm-openai:v1",
    *OPTIONS,
]
INSPECT = ["docker", "container", "inspect", "--format", "{{.State.Status}}", "model"]
REMOVE = ["docker", "rm", "--force", "model"]


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A model file on a machine whose commands are recorded and not run.

    ``answers`` maps a command's second word to the exit code and output it
    gets; a command not named succeeds with no output. ``pid`` is the process
    id of the model's running server, ``None`` for none. The server does not
    answer unless a test says so.
    """
    path = tmp_path / "model.yaml"
    path.write_text(MODEL)
    stand_in = SimpleNamespace(
        path=str(path),
        state=tmp_path / "state",
        calls=[],
        answers={},
        started=[],
        pid=None,
    )

    def run(command, **_):
        stand_in.calls.append(command)
        code, out = stand_in.answers.get(command[1], (0, ""))
        return SimpleNamespace(returncode=code, stdout=out)

    def popen(command, **options):
        stand_in.started.append((command, options))
        return SimpleNamespace(pid=4242)

    def no_answer(url, **_):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(server, "STATE", stand_in.state)
    monkeypatch.setattr(server, "_process", lambda name: stand_in.pid)
    monkeypatch.setattr(server.shutil, "which", lambda name: f"/bin/{Path(name).name}")
    monkeypatch.setattr(server.subprocess, "run", run)
    monkeypatch.setattr(server.subprocess, "Popen", popen)
    monkeypatch.setattr(server.httpx, "get", no_answer)
    return stand_in


def test_the_first_start_installs_vllm_then_starts_it_in_the_background(
    machine, capsys
):
    cli.main(["server", machine.path])
    environment = machine.state / "vllm-1"
    assert machine.calls == [
        ["/bin/uv", "venv", str(environment), "--python=3.12", "--seed"]
        + ["--managed-python", "--allow-existing"],
        ["/bin/uv", "pip", "install", f"--python={environment}/bin/python"]
        + ["vllm==1", "--torch-backend=auto"],
        [f"{environment}/bin/vllm", "download-kernels"],
    ]
    [(command, options)] = machine.started
    assert command == [
        f"{environment}/bin/vllm",
        "serve",
        *OPTIONS,
        "--host=127.0.0.1",
        "--port=8123",
    ]
    assert options["start_new_session"] and "HF_HOME" not in options["env"]
    assert (machine.state / "model.pid").read_text() == "4242"
    out = capsys.readouterr().out
    assert "model: loading" in out and f"tail -f {machine.state}/model.log" in out


def test_an_installed_vllm_is_not_installed_again(machine):
    cli.main(["server", machine.path])
    cli.main(["server", machine.path, "--stop"])
    cli.main(["server", machine.path])
    assert len(machine.calls) == 3 and len(machine.started) == 2


def test_an_install_cut_short_starts_nothing_and_is_taken_up_again(machine):
    machine.answers["pip"] = (2, "")
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["server", machine.path])
    assert exit_info.value.code == 2 and machine.started == []
    del machine.answers["pip"]
    cli.main(["server", machine.path])
    assert len(machine.calls) == 2 + 3 and len(machine.started) == 1


def test_kernels_that_cannot_be_fetched_do_not_stop_the_start(machine, capsys):
    machine.answers["download-kernels"] = (1, "")
    cli.main(["server", machine.path])
    assert len(machine.calls) == 3 and len(machine.started) == 1
    assert "kernels were not installed" in capsys.readouterr().out
    assert (machine.state / "vllm-1" / "installed").exists()


def test_the_cache_option_sets_where_the_weights_are_kept(machine):
    cli.main(["server", machine.path, "--cache", "/data/weights"])
    assert machine.started[0][1]["env"]["HF_HOME"] == "/data/weights"
    machine.answers["container"] = (1, "")
    cli.main(["server", machine.path, "--docker", "--cache", "/data/weights"])
    assert "--volume=/data/weights:/root/.cache/huggingface" in machine.calls[-1]


def test_a_running_server_is_left_alone_and_reported_when_it_answers(
    machine, monkeypatch, capsys
):
    machine.pid = 99
    asked = []
    monkeypatch.setattr(server.httpx, "get", lambda url, **_: asked.append(url))
    cli.main(["server", machine.path])
    assert machine.calls == [] and machine.started == []
    assert asked == ["http://localhost:8123/v1/models"]
    assert "model: ready at http://localhost:8123/v1" in capsys.readouterr().out


def test_a_server_that_stopped_by_itself_is_not_started_again(machine):
    machine.state.mkdir()
    (machine.state / "model.pid").write_text("99")
    with pytest.raises(SystemExit, match="stopped by itself.*model.log"):
        cli.main(["server", machine.path])
    assert machine.started == []


def test_stop_ends_the_server_and_its_workers(machine, monkeypatch):
    machine.state.mkdir()
    (machine.state / "model.pid").write_text("99")
    machine.pid = 99
    signals = []

    def killpg(pid, sent):
        signals.append((pid, sent))
        machine.pid = None

    monkeypatch.setattr(server.os, "killpg", killpg)
    cli.main(["server", machine.path, "--stop"])
    assert signals == [(99, signal.SIGTERM)]
    assert not (machine.state / "model.pid").exists()
    # With nothing to stop, the command does nothing and does not fail.
    cli.main(["server", machine.path, "--stop"])
    assert signals == [(99, signal.SIGTERM)]


def test_the_server_needs_uv(machine, monkeypatch):
    monkeypatch.setattr(server.shutil, "which", lambda name: None)
    with pytest.raises(SystemExit, match="uv not found"):
        cli.main(["server", machine.path])


def test_only_a_live_vllm_process_counts_as_the_server(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STATE", tmp_path)
    assert server._process("model") is None
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "vllm"]
    )
    try:
        (tmp_path / "model.pid").write_text(str(sleeper.pid))
        assert server._process("model") == sleeper.pid
    finally:
        sleeper.kill()
        sleeper.wait()
    # The id is free again, or belongs to a program that is not the server.
    assert server._process("model") is None
    (tmp_path / "model.pid").write_text("1")
    assert server._process("model") is None


def test_docker_starts_the_image_of_the_same_release(machine, capsys):
    machine.answers["container"] = (1, "")
    cli.main(["server", machine.path, "--docker"])
    assert machine.calls == [INSPECT, DOCKER_RUN] and machine.started == []
    out = capsys.readouterr().out
    assert "model: loading" in out and "docker logs -f model" in out


def test_docker_leaves_a_running_container_alone(machine):
    machine.answers["container"] = (0, "running\n")
    cli.main(["server", machine.path, "--docker"])
    assert machine.calls == [INSPECT]


def test_docker_keeps_a_stopped_container_for_its_log(machine):
    machine.answers["container"] = (0, "exited\n")
    with pytest.raises(SystemExit, match="is exited.*docker logs model"):
        cli.main(["server", machine.path, "--docker"])
    assert machine.calls == [INSPECT]


def test_a_docker_start_that_fails_removes_its_container(machine):
    machine.answers.update(container=(1, ""), run=(125, ""))
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["server", machine.path, "--docker"])
    assert exit_info.value.code == 125
    assert machine.calls == [INSPECT, DOCKER_RUN, REMOVE]


def test_docker_stop_removes_the_container(machine):
    cli.main(["server", machine.path, "--docker", "--stop"])
    assert machine.calls == [REMOVE]


def test_docker_is_needed_only_when_asked_for(machine, monkeypatch):
    monkeypatch.setattr(
        server.shutil, "which", lambda name: None if name == "docker" else "/bin/uv"
    )
    with pytest.raises(SystemExit, match="docker not found"):
        cli.main(["server", machine.path, "--docker"])
    cli.main(["server", machine.path])
    assert len(machine.started) == 1


def test_a_model_file_without_a_server_block_is_refused(machine):
    with pytest.raises(SystemExit, match="no server block"):
        cli.main(["server", str(runner.config_path("models", "haiku-5-5"))])
    assert machine.calls == [] and machine.started == []


def test_the_qwen_file_names_one_release_and_one_option_an_item():
    block = yaml.safe_load(runner.config_path("models", "qwen3.6-27b").read_text())
    assert isinstance(block["server"]["vllm"], str)
    assert all(a.startswith("--") and " " not in a for a in block["server"]["args"])
