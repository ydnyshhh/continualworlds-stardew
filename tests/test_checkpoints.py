import json
from pathlib import Path

import pytest

import prime_stardew.env.checkpoints as checkpoint_module

from prime_stardew.env.checkpoints import CheckpointError, CheckpointManager
from prime_stardew.env.errors import ConfigurationError
from prime_stardew.env.models import GameDate


SAVE_ID = "PrimeStardewSmoke_406041616"


def make_save(root: Path) -> Path:
    save = root / SAVE_ID
    save.mkdir(parents=True)
    (save / SAVE_ID).write_text("primary-save", encoding="utf-8")
    (save / f"{SAVE_ID}_old").write_text("old-save", encoding="utf-8")
    (save / "SaveGameInfo").write_text("info", encoding="utf-8")
    return save


def test_create_verify_and_restore_with_renamed_save_files(tmp_path: Path) -> None:
    saves = tmp_path / "saves"
    make_save(saves)
    manager = CheckpointManager(saves, stable_interval=0)
    checkpoint = tmp_path / "checkpoints" / "spring-06"

    created = manager.create(
        SAVE_ID,
        checkpoint,
        player="PrimeStardewSmoke",
        game_date=GameDate(year=1, season="spring", day=6),
        environment={"game_version": "1.6.15.24356"},
    )
    verified = manager.verify(checkpoint)
    restored = manager.restore(checkpoint, "ExperimentA_123")

    assert verified == created
    assert (restored / "ExperimentA_123").read_text(encoding="utf-8") == "primary-save"
    assert (restored / "ExperimentA_123_old").read_text(encoding="utf-8") == "old-save"
    assert (restored / "SaveGameInfo").read_text(encoding="utf-8") == "info"
    assert json.loads((checkpoint / "manifest.json").read_text())["source_save_id"] == SAVE_ID


def test_verify_detects_tampering(tmp_path: Path) -> None:
    saves = tmp_path / "saves"
    make_save(saves)
    manager = CheckpointManager(saves, stable_interval=0)
    checkpoint = tmp_path / "checkpoint"
    manager.create(
        SAVE_ID,
        checkpoint,
        player="PrimeStardewSmoke",
        game_date=GameDate(year=1, season="spring", day=6),
    )
    (checkpoint / "SaveGameInfo").write_text("tampered", encoding="utf-8")

    with pytest.raises(CheckpointError, match="hash mismatch"):
        manager.verify(checkpoint)


def test_never_overwrites_checkpoint_or_restore_destination(tmp_path: Path) -> None:
    saves = tmp_path / "saves"
    make_save(saves)
    manager = CheckpointManager(saves, stable_interval=0)
    checkpoint = tmp_path / "checkpoint"
    manager.create(
        SAVE_ID,
        checkpoint,
        player="PrimeStardewSmoke",
        game_date=GameDate(year=1, season="spring", day=6),
    )

    with pytest.raises(CheckpointError, match="already exists"):
        manager.create(
            SAVE_ID,
            checkpoint,
            player="PrimeStardewSmoke",
            game_date=GameDate(year=1, season="spring", day=6),
        )
    with pytest.raises(CheckpointError, match="already exists"):
        manager.restore(checkpoint, SAVE_ID)


def test_rejects_path_traversal_in_manifest(tmp_path: Path) -> None:
    saves = tmp_path / "saves"
    make_save(saves)
    manager = CheckpointManager(saves, stable_interval=0)
    checkpoint = tmp_path / "checkpoint"
    manager.create(
        SAVE_ID,
        checkpoint,
        player="PrimeStardewSmoke",
        game_date=GameDate(year=1, season="spring", day=6),
    )
    manifest = json.loads((checkpoint / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "../outside"
    (checkpoint / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ConfigurationError, match="escapes managed root"):
        manager.verify(checkpoint)


def test_create_retries_transient_locked_save_file(tmp_path: Path, monkeypatch) -> None:
    saves = tmp_path / "saves"
    make_save(saves)
    real_sha256 = checkpoint_module._sha256
    calls = 0

    def transient_sha256(path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("simulated Stardew save lock")
        return real_sha256(path)

    monkeypatch.setattr(checkpoint_module, "_sha256", transient_sha256)
    manager = CheckpointManager(saves, stable_interval=0, stable_timeout=1)

    manifest = manager.create(
        SAVE_ID,
        tmp_path / "checkpoint",
        player="PrimeStardewSmoke",
        game_date=GameDate(year=1, season="spring", day=6),
    )

    assert calls > 1
    assert manifest.files


def test_create_waits_for_stardew_temporary_file_to_disappear(tmp_path: Path, monkeypatch) -> None:
    saves = tmp_path / "saves"
    save = make_save(saves)
    temporary = save / f"{SAVE_ID}_STARDEWVALLEYSAVETMP"
    temporary.write_text("in progress", encoding="utf-8")
    real_sleep = checkpoint_module.time.sleep
    sleeps = 0

    def remove_during_wait(_interval):
        nonlocal sleeps
        sleeps += 1
        if temporary.exists():
            temporary.unlink()
        real_sleep(0)

    monkeypatch.setattr(checkpoint_module.time, "sleep", remove_during_wait)
    manager = CheckpointManager(saves, stable_interval=0, stable_timeout=1)
    manifest = manager.create(
        SAVE_ID,
        tmp_path / "checkpoint",
        player="PrimeStardewSmoke",
        game_date=GameDate(year=1, season="spring", day=6),
    )

    assert sleeps >= 1
    assert all("STARDEWVALLEYSAVETMP" not in entry.path for entry in manifest.files)
