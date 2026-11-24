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
    id of the model's running server, ``None`` for none, and ``cards`` what
    ``nvidia-smi`` lists, which is asked and not recorded. Nothing listens on
    the server's port and the server does not answer unless a test says so.
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
        cards="",
    )

    def run(command, **_):
        if command[0] == "nvidia-smi":
            return SimpleNamespace(returncode=0, stdout=stand_in.cards)
        stand_in.calls.append(command)
        code, out = stand_in.answers.get(command[1], (0, ""))
        return SimpleNamespace(returncode=code, stdout=out, stderr="")

    def popen(command, **options):
        stand_in.started.append((command, options))
        return SimpleNamespace(pid=4242)

    def no_answer(url, **_):
        raise httpx.ConnectError("refused")

    def refused(address, timeout):
        raise ConnectionRefusedError

    monkeypatch.setattr(server, "STATE", stand_in.state)
    monkeypatch.setattr(server, "_process", lambda name: stand_in.pid)
    monkeypatch.setattr(server.shutil, "which", lambda name: f"/bin/{Path(name).name}")
    monkeypatch.setattr(server.subprocess, "run", run)
    monkeypatch.setattr(server.subprocess, "Popen", popen)
    monkeypatch.setattr(server.httpx, "get", no_answer)
    monkeypatch.setattr(server.socket, "create_connection", refused)
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

    def lists_the_model(url, **_):
        asked.append(url)
        return SimpleNamespace(status_code=200, text='{"data": [{"id": "org/model"}]}')

    monkeypatch.setattr(server.httpx, "get", lists_the_model)
    cli.main(["server", machine.path])
    assert machine.calls == [] and machine.started == []
    assert asked == ["http://localhost:8123/v1/models"]
    assert "model: ready at http://localhost:8123/v1" in capsys.readouterr().out


@pytest.mark.parametrize(
    "answer",
    [
        SimpleNamespace(status_code=404, text="Nothing matches the given URI"),
        SimpleNamespace(status_code=200, text='{"data": [{"id": "another/model"}]}'),
    ],
)
def test_another_program_answering_at_the_address_is_not_the_server(
    machine, monkeypatch, capsys, answer
):
    machine.pid = 99
    monkeypatch.setattr(server.httpx, "get", lambda url, **_: answer)
    cli.main(["server", machine.path])
    assert "model: loading" in capsys.readouterr().out


def test_a_port_another_program_uses_stops_the_start(machine, monkeypatch):
    listening = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(
        server.socket, "create_connection", lambda address, timeout: listening
    )
    for route in ([], ["--docker"]):
        machine.answers["container"] = (1, "")
        with pytest.raises(SystemExit, match="port 8123 is already in use"):
            cli.main(["server", machine.path, *route])
    assert machine.started == [] and machine.calls == [INSPECT]
    # The port of a server that is already running is its own.
    machine.pid = 99
    cli.main(["server", machine.path])


def test_a_server_that_stopped_by_itself_is_not_started_again(machine):
    machine.state.mkdir()
    (machine.state / "model.pid").write_text("99")
    (machine.state / "model.log").write_text(
        "(EngineCore pid=7) ERROR 11-23 [core.py:1] ValueError: too long, at most 9\n"
        "    raise ValueError(\n"
        "ValueError: too long, at most 9\n"
        "RuntimeError: Engine core initialization failed.\n"
    )
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["server", machine.path])
    # The errors its output names, each once, then where the output is.
    assert str(exit_info.value) == (
        "error: the server of model stopped by itself\n"
        "  ValueError: too long, at most 9\n"
        "  RuntimeError: Engine core initialization failed.\n"
        f"Its whole output: {machine.state}/model.log\n"
        f"Then clear it with: mas-optimiser server {machine.path} --stop"
    )
    assert machine.started == []


def test_the_gpus_option_limits_the_server_to_the_cards_named(machine):
    cli.main(["server", machine.path, "--gpus", "2,3"])
    assert machine.started[0][1]["env"]["CUDA_VISIBLE_DEVICES"] == "2,3"
    machine.answers["container"] = (1, "")
    cli.main(["server", machine.path, "--docker", "--gpus", "2,3"])
    assert '--gpus="device=2,3"' in machine.calls[-1]


def test_a_start_warns_of_cards_other_programs_already_use(machine, capsys):
    machine.cards = "0, 43184, 46068\n1, 3000, 46068\n2, 40000, 46068\n"
    cli.main(["server", machine.path, "--gpus", "0,1"])
    out = capsys.readouterr().out
    assert "warning: card 0 already has 43,184 of 46,068 MiB in use" in out
    # A card with room, and a card the server was not given, are not named.
    assert "card 1" not in out and "card 2" not in out
    assert len(machine.started) == 1


def test_a_start_warns_when_the_machine_shows_no_nvidia_gpu(
    machine, monkeypatch, capsys
):
    monkeypatch.setattr(
        server.shutil,
        "which",
        lambda name: None if name == "nvidia-smi" else f"/bin/{Path(name).name}",
    )
    cli.main(["server", machine.path])
    assert "warning: nvidia-smi not found" in capsys.readouterr().out


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


def test_docker_keeps_a_stopped_container_and_says_why_it_stopped(machine):
    machine.answers["container"] = (0, "exited\n")
    machine.answers["logs"] = (0, "torch.OutOfMemoryError: CUDA out of memory.\n")
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["server", machine.path, "--docker"])
    assert str(exit_info.value) == (
        "error: the server of model stopped by itself\n"
        "  torch.OutOfMemoryError: CUDA out of memory.\n"
        "Its whole output: docker logs model\n"
        f"Then clear it with: mas-optimiser server {machine.path} --docker --stop"
    )
    assert machine.calls == [INSPECT, ["docker", "logs", "--tail=400", "model"]]


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


@pytest.mark.parametrize("name", ["qwen3.6-27b", "qwen3.6-27b-fp8", "gpt-oss-20b"])
def test_a_served_models_file_names_one_release_and_one_option_an_item(name):
    path = runner.config_path("models", name)
    block = yaml.safe_load(path.read_text())
    assert isinstance(block["server"]["vllm"], str)
    assert all(a.startswith("--") and " " not in a for a in block["server"]["args"])
    # It loads as a model that asks for no key.
    config, _ = runner.load_model(path)
    assert (config.llm_provider, config.llm_api_key) == ("vllm", "")


def test_the_fp8_file_differs_only_in_the_model_the_port_and_the_cards():
    released, fp8 = (
        yaml.safe_load(runner.config_path("models", name).read_text())
        for name in ("qwen3.6-27b", "qwen3.6-27b-fp8")
    )
    assert {key for key in released if released[key] != fp8[key]} == {
        "llm_model",
        "llm_api_endpoint",
        "server",
    }
    assert fp8["llm_model"] == "Qwen/Qwen3.6-27B-FP8"
    assert released["server"]["vllm"] == fp8["server"]["vllm"]
    # The same options but for the number of cards, so that results on the
    # two differ by the model alone.
    options = [
        [a for a in block["server"]["args"] if "tensor-parallel-size" not in a]
        for block in (released, fp8)
    ]
    assert options[0] == options[1]
