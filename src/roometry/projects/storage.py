"""Durable, private normalized photos. Guest cleanup never traverses this root."""

import shutil
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import httpx


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


class SupabaseProjectPhotos:
    """Private object storage, accessed only by the authenticated backend."""

    def __init__(self, url, bucket, secret, *, transport=None):
        self.url = url.rstrip("/") + "/storage/v1"
        self.bucket = bucket
        self.secret = secret
        self.transport = transport

    def _request(self, method, path, **kwargs):
        try:
            with httpx.Client(timeout=30, transport=self.transport, follow_redirects=False) as client:
                response = client.request(method, self.url + path, headers={
                    "apikey": self.secret, "Authorization": "Bearer " + self.secret,
                    **kwargs.pop("headers", {}),
                }, **kwargs)
                response.raise_for_status()
                return response
        except httpx.HTTPError as error:
            raise OSError("Private photo storage is unavailable") from error

    @staticmethod
    def _key(key):
        try:
            batch, name = key.split("/")
            image = name.removesuffix(".jpg")
            if str(UUID(batch)) != batch or str(UUID(image)) != image or name != image + ".jpg":
                raise ValueError()
        except (ValueError, AttributeError) as error:
            raise OSError("Invalid private image key") from error
        return key

    def write(self, images):
        batch, records = str(uuid4()), []
        try:
            for index, (metadata, data) in enumerate(images):
                image_id = uuid4()
                key = f"{batch}/{image_id}.jpg"
                self._request("POST", f"/object/{self.bucket}/{key}", content=data,
                              headers={"Content-Type": "image/jpeg", "x-upsert": "false"})
                records.append({"id": image_id, "source_image_id": metadata.image_id,
                                "storage_key": key, "sha256": sha256(data).hexdigest(),
                                "sort_order": index, "width": metadata.prepared_width_px,
                                "height": metadata.prepared_height_px, "byte_count": len(data)})
        except BaseException:
            try:
                self.discard(batch)
            except OSError:
                pass
            raise
        return batch, records

    def read(self, key, digest):
        key = self._key(key)
        data = self._request("GET", f"/object/authenticated/{self.bucket}/{key}").content
        if sha256(data).hexdigest() != digest:
            raise OSError("Private image integrity check failed")
        return data

    def discard(self, batch):
        try:
            if str(UUID(batch)) != batch:
                raise ValueError()
        except (ValueError, AttributeError) as error:
            raise OSError("Invalid private image batch") from error
        # Each batch contains at most the configured upload limit. Listing is
        # repeated after deletion so no page can be skipped when records shift.
        for _ in range(10):
            try:
                files = self._request("POST", f"/object/list/{self.bucket}",
                                      json={"prefix": batch, "limit": 100, "offset": 0}).json()
                if not isinstance(files, list):
                    raise ValueError()
                keys = [self._key(f"{batch}/{item['name']}") for item in files]
            except (KeyError, TypeError, ValueError) as error:
                raise OSError("Invalid private photo listing") from error
            if not keys:
                return
            self._request("DELETE", f"/object/{self.bucket}", json={"prefixes": keys})
        raise OSError("Private photo cleanup did not complete")
