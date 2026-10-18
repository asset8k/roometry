"""Normalize batches off the event loop; commit only a completely valid batch."""

from datetime import timedelta
from functools import partial
from pathlib import Path
from uuid import uuid4

import anyio
from starlette.datastructures import UploadFile

from roometry.uploads.errors import UploadFailure
from roometry.uploads.schemas import (
    PreparedImage,
    PreparedRoomInput,
    RoomInputOptions,
    UploadLimits,
)
from roometry.uploads.storage import InputStorage
from roometry.uploads.validation import initialize_decoders, normalize_photo


class UploadService:
    def __init__(self, limits: UploadLimits, storage: InputStorage):
        self.limits, self.storage = limits, storage
        # Serialize decoding and batch replacement per worker. Multipart files
        # spool separately; public deployment still needs ingress abuse limits.
        self._capacity = anyio.CapacityLimiter(1)

    async def start(self) -> None:
        await anyio.to_thread.run_sync(initialize_decoders)
        await anyio.to_thread.run_sync(self.storage.initialize)

    async def cleanup(self) -> None:
        await anyio.to_thread.run_sync(self.storage.cleanup)

    async def prepare(
        self, files: list[UploadFile], options: RoomInputOptions, owner: str
    ) -> PreparedRoomInput:
        async with self._capacity:
            return await anyio.to_thread.run_sync(
                partial(self._prepare, files, options, owner)
            )

    def _prepare(
        self, files: list[UploadFile], options: RoomInputOptions, owner: str
    ) -> PreparedRoomInput:
        stage: Path | None = None
        try:
            self.storage.cleanup()
            stage = self.storage.stage()
            images: list[PreparedImage] = []
            total = 0
            for index, upload in enumerate(files):
                anyio.from_thread.check_cancelled()
                upload.file.seek(0)
                data = upload.file.read(self.limits.max_file_bytes + 1)
                if len(data) > self.limits.max_file_bytes:
                    raise UploadFailure(
                        413,
                        "file_too_large",
                        "This photo exceeds the file-size limit.",
                        file_index=index,
                    )
                total += len(data)
                if total > self.limits.max_total_file_bytes:
                    raise UploadFailure(
                        413,
                        "total_files_too_large",
                        "The combined photos exceed the upload-size limit.",
                    )
                photo = normalize_photo(
                    data, upload.filename or "", upload.content_type, self.limits, index
                )
                self.storage.write_image(stage, photo.metadata.image_id, photo.jpeg)
                images.append(photo.metadata)
            anyio.from_thread.check_cancelled()
            public = PreparedRoomInput(
                input_id=str(uuid4()),
                status="ready",
                options=options,
                images=images,
                expires_at=self.storage.now()
                + timedelta(seconds=self.limits.ttl_seconds),
            )
            self.storage.commit(stage, owner, public)
            stage = None
            return public
        except OSError:
            raise UploadFailure(
                503,
                "storage_unavailable",
                "Photo storage is temporarily unavailable. Please try again.",
            ) from None
        finally:
            if stage is not None:
                try:
                    self.storage.discard_stage(stage)
                except OSError:
                    pass  # Periodic/startup cleanup retries failed staging deletion.

    async def delete(self, input_id: str, owner: str) -> None:
        async with self._capacity:
            try:
                await anyio.to_thread.run_sync(self.storage.delete, input_id, owner)
            except OSError:
                raise UploadFailure(
                    503,
                    "storage_unavailable",
                    "Photo storage is temporarily unavailable. Please try again.",
                ) from None
