"""Private local guest storage. One process only; restart discards guest inputs."""

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Protocol
from uuid import UUID, uuid4

from roometry.uploads.errors import UploadFailure
from roometry.uploads.schemas import PreparedRoomInput


def utc_now() -> datetime:
    return datetime.now(UTC)


def canonical_id(value: str) -> bool:
    try:
        parsed = UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except ValueError:
        return False


@dataclass(frozen=True)
class StoredInput:
    owner: str
    public: PreparedRoomInput
    directory: Path


class InputStorage(Protocol):
    def now(self) -> datetime: ...
    def initialize(self) -> None: ...
    def stage(self) -> Path: ...
    def write_image(self, stage: Path, image_id: str, data: bytes) -> None: ...
    def discard_stage(self, stage: Path) -> None: ...
    def commit(self, stage: Path, owner: str, public: PreparedRoomInput) -> None: ...
    def resolve(self, input_id: str, owner: str) -> PreparedRoomInput: ...
    def read_image(self, input_id: str, image_id: str, owner: str) -> bytes: ...
    def delete(self, input_id: str, owner: str) -> None: ...
    def cleanup(self) -> None: ...


class LocalInputStorage:
    def __init__(self, root: Path, clock: Callable[[], datetime] = utc_now):
        self.root = root.absolute()
        self.now = clock
        self._records: dict[str, StoredInput] = {}
        self._owners: dict[str, str] = {}
        self._stages: set[Path] = set()
        self._lock = RLock()
        self._initialized = False

    def _remove(self, path: Path) -> None:
        if path.parent != self.root:
            raise OSError("Invalid managed storage path")
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.exists():
            shutil.rmtree(path)

    def initialize(self) -> None:
        with self._lock:
            if self._initialized:
                return
            if self.root.is_symlink():
                raise OSError("Upload root cannot be a symlink")
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.root.chmod(0o700)
            # Ownership is intentionally ephemeral. A new process cannot reopen
            # a prior guest input, so remove its bytes and crash-left staging.
            for path in self.root.iterdir():
                if canonical_id(path.name) or (
                    path.name.startswith(".staging-") and canonical_id(path.name[9:])
                ):
                    self._remove(path)
            self._initialized = True

    def stage(self) -> Path:
        with self._lock:
            self.initialize()
            path = self.root / f".staging-{uuid4()}"
            path.mkdir(mode=0o700)
            self._stages.add(path)
            return path

    def write_image(self, stage: Path, image_id: str, data: bytes) -> None:
        with self._lock:
            if stage not in self._stages or not canonical_id(image_id):
                raise OSError("Invalid image storage target")
            path = stage / f"{image_id}.jpg"
            with path.open("xb") as target:
                path.chmod(0o600)
                target.write(data)

    def discard_stage(self, stage: Path) -> None:
        with self._lock:
            try:
                self._remove(stage)
            finally:
                self._stages.discard(stage)

    def commit(self, stage: Path, owner: str, public: PreparedRoomInput) -> None:
        with self._lock:
            if stage not in self._stages or not canonical_id(public.input_id):
                raise OSError("Invalid batch storage target")
            directory = self.root / public.input_id
            stored = StoredInput(owner, public.model_copy(deep=True), directory)
            stage.rename(directory)
            self._stages.discard(stage)
            old = self._owners.get(owner)
            self._records[public.input_id] = stored
            self._owners[owner] = public.input_id
            if old:
                # Revoke access immediately; filesystem deletion may need retry.
                previous = self._records.pop(old)
                try:
                    self._remove(previous.directory)
                except OSError:
                    pass  # cleanup() retries unreferenced managed directories.

    def _owned(self, input_id: str, owner: str) -> StoredInput:
        record = self._records.get(input_id)
        if (
            record is None
            or record.owner != owner
            or record.public.expires_at <= self.now()
        ):
            raise UploadFailure(
                404, "input_not_found", "This room input is unavailable or has expired."
            )
        return record

    def resolve(self, input_id: str, owner: str) -> PreparedRoomInput:
        with self._lock:
            return self._owned(input_id, owner).public.model_copy(deep=True)

    def read_image(self, input_id: str, image_id: str, owner: str) -> bytes:
        with self._lock:
            record = self._owned(input_id, owner)
            if image_id not in {image.image_id for image in record.public.images}:
                raise UploadFailure(
                    404, "input_not_found", "This image is unavailable."
                )
            path = record.directory / f"{image_id}.jpg"
            if path.is_symlink():
                raise OSError("Invalid image storage path")
            return path.read_bytes()

    def delete(self, input_id: str, owner: str) -> None:
        with self._lock:
            record = self._owned(input_id, owner)
            self._records.pop(input_id)
            self._owners.pop(record.owner, None)
            self._remove(record.directory)

    def cleanup(self) -> None:
        with self._lock:
            self.initialize()
            for input_id, record in list(self._records.items()):
                if record.public.expires_at <= self.now():
                    self._records.pop(input_id)
                    self._owners.pop(record.owner, None)
            failure: OSError | None = None
            for path in self.root.iterdir():
                orphan = canonical_id(path.name) and path.name not in self._records
                staging = (
                    path.name.startswith(".staging-")
                    and canonical_id(path.name[9:])
                    and path not in self._stages
                )
                if orphan or staging:
                    try:
                        self._remove(path)
                    except OSError as error:
                        failure = error
            if failure is not None:
                raise failure
