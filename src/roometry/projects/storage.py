"""Durable, private normalized photos. Guest cleanup never traverses this root."""

import shutil
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4


class ProjectPhotos:
    def __init__(self, root: Path):
        self.root = root.absolute()

    def _root(self):
        if self.root.is_symlink():
            raise OSError("Invalid private image root")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)

    def write(self, images):
        self._root()
        batch = str(uuid4())
        directory = self.root / batch
        directory.mkdir(mode=0o700)
        records = []
        try:
            for index, (metadata, data) in enumerate(images):
                image_id = uuid4()
                path = directory / f"{image_id}.jpg"
                with path.open("xb") as target:
                    path.chmod(0o600)
                    target.write(data)
                records.append(
                    {
                        "id": image_id,
                        "source_image_id": metadata.image_id,
                        "storage_key": f"{batch}/{image_id}.jpg",
                        "sha256": sha256(data).hexdigest(),
                        "sort_order": index,
                        "width": metadata.prepared_width_px,
                        "height": metadata.prepared_height_px,
                        "byte_count": len(data),
                    }
                )
        except BaseException:
            self.discard(batch)
            raise
        return batch, records

    def discard(self, batch):
        if str(UUID(batch)) != batch:
            raise OSError("Invalid private image batch")
        directory = self.root / batch
        if directory.is_symlink():
            raise OSError("Invalid private image batch")
        shutil.rmtree(directory, ignore_errors=True)

    def read(self, key, digest):
        batch, name = key.split("/")
        image_id = name.removesuffix(".jpg")
        if (
            str(UUID(batch)) != batch
            or str(UUID(image_id)) != image_id
            or name != f"{image_id}.jpg"
        ):
            raise OSError("Invalid private image key")
        directory, path = self.root / batch, self.root / batch / name
        if self.root.is_symlink() or directory.is_symlink() or path.is_symlink():
            raise OSError("Invalid private image path")
        data = path.read_bytes()
        if sha256(data).hexdigest() != digest:
            raise OSError("Private image integrity check failed")
        return data
