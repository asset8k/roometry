"""Explicit private snapshots. Saving reads existing runs; it never invokes a model."""

import copy
import json
import logging
from datetime import UTC, datetime
from functools import wraps
from hashlib import sha256
from uuid import uuid4

import anyio
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from roometry.agents.checkpointing import RunUnavailable
from roometry.auth.errors import AuthFailure
from roometry.optimization.context import validate_context
from roometry.projects.model import Project, ProjectImage, ProjectSave, SceneVersion
from roometry.projects.schemas import SavedProject
from roometry.scenes.schemas import RoomScene
from roometry.scenes.validation import validate_geometry


def failure(status, code, message):
    return AuthFailure(status, code, message)


def guarded(method):
    @wraps(method)
    def call(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except SQLAlchemyError:
            raise failure(
                503,
                "project_unavailable",
                "Room saving is temporarily unavailable. Please try again.",
            ) from None

    return call


def fingerprint(value):
    return sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


class ProjectService:
    def __init__(self, database, photos, uploads, reconstructions, improvements):
        self.database, self.photos, self.uploads = database, photos, uploads
        self.reconstructions, self.improvements = reconstructions, improvements

    def available(self):
        if self.database is None:
            raise failure(
                503, "project_unavailable", "Room saving is not configured yet."
            )

    def _owned(self, db, project_id, user_id):
        project = db.scalar(
            select(Project)
            .where(Project.id == project_id, Project.user_id == user_id)
            .with_for_update()
        )
        if project is None:
            raise failure(404, "project_not_found", "This saved room is unavailable.")
        return project

    def _receipt(self, db, user_id, body, body_hash):
        receipt = db.scalar(
            select(ProjectSave).where(
                ProjectSave.user_id == user_id,
                ProjectSave.request_id == body.request_id,
            )
        )
        if receipt:
            if receipt.body_hash != body_hash:
                raise failure(
                    409,
                    "save_conflict",
                    "This save identifier was already used. Start a new save for changed details.",
                )
            return SavedProject.model_validate(receipt.response_json)
        return None

    @guarded
    def preflight(self, user_id, body, body_hash):
        self.available()
        with self.database.sessions() as db:
            receipt = self._receipt(db, user_id, body, body_hash)
            if receipt:
                return receipt
            if body.project_id is None:
                return None
            project = self._owned(db, body.project_id, user_id)
            if (
                project.revision != body.expected_revision
                or project.source_run_id != body.source_run_id
            ):
                raise failure(
                    409,
                    "project_changed",
                    "This room has changed. Reopen the latest saved room before saving again.",
                )
            return copy.deepcopy(project.snapshot)

    async def save(self, user_id, owner, body):
        body_hash = fingerprint(body.model_dump(mode="json"))
        previous = await anyio.to_thread.run_sync(
            self.preflight, user_id, body, body_hash
        )
        if isinstance(previous, SavedProject):
            return previous
        try:
            if previous is None:
                source = await self.reconstructions.read(body.source_run_id, owner)
                if (
                    source.status not in {"completed", "partial"}
                    or source.working_scene is None
                ):
                    raise failure(
                        409,
                        "scene_required",
                        "Finish reconstructing a room before saving it.",
                    )
                source = source.model_dump(mode="json")
                known = {}
            else:
                source = previous["source"]
                known = previous["improvements"]
            improvements = {}
            for run_id in body.improvement_run_ids:
                result = known.get(run_id)
                if result is None:
                    result = (await self.improvements.read(run_id, owner)).model_dump(
                        mode="json"
                    )
                if (
                    result["source_run_id"] != body.source_run_id
                    or result["status"] in {"prepared", "running", "no_improvement"}
                    or result["result_scene"] is None
                    or not any(a["outcome"] == "accepted" for a in result["attempts"])
                ):
                    raise failure(
                        422,
                        "invalid_candidate",
                        "Save only retained improvements from this room.",
                    )
                improvements[run_id] = result
            return await anyio.to_thread.run_sync(
                self.persist,
                user_id,
                owner,
                body,
                body_hash,
                source,
                improvements,
                previous is None,
            )
        except RunUnavailable:
            raise failure(
                404,
                "source_expired",
                "The temporary room is unavailable. Save while its photos are still available.",
            ) from None

    @guarded
    def persist(self, user_id, owner, body, body_hash, source, improvements, is_new):
        bases = {"original": RoomScene.model_validate(source["working_scene"])}
        versions = [("original", "base", None, bases["original"])]
        pending = dict(improvements)
        while pending:
            ready = [
                key
                for key, value in pending.items()
                if (value.get("parent_run_id") or "original") in bases
            ]
            if not ready:
                raise failure(
                    422,
                    "invalid_parent",
                    "Include the retained parent scenes for these improvements.",
                )
            for run_id in ready:
                result = pending.pop(run_id)
                parent = result.get("parent_run_id") or "original"
                context = RoomScene.model_validate(result["source_scene"])
                # Keep the exact edited/locked starting scene, not just a link to its older baseline.
                validate_context(bases[parent], context)
                context_key = f"context:{run_id}"
                versions.append((context_key, "manual", parent, context))
                base = RoomScene.model_validate(result["result_scene"])
                bases[run_id] = base
                versions.append(
                    (
                        run_id,
                        "refined" if result["instruction"] else "optimized",
                        context_key,
                        base,
                    )
                )
        for item in body.manual_copies:
            parent = item.parent_run_id or "original"
            if parent not in bases:
                raise failure(
                    422,
                    "invalid_parent",
                    "An editable scene must refer to a retained version of this room.",
                )
            try:
                validate_context(bases[parent], item.scene)
            except ValueError:
                raise failure(
                    422,
                    "invalid_manual_scene",
                    "An edited scene must preserve room measurements, furniture identities, fixed objects and valid placement.",
                ) from None
            versions.append((f"manual:{parent}", "manual", parent, item.scene))
        current = (
            "original"
            if body.view == "original"
            else body.selected_improvement_id
            if body.view == "improved"
            else f"manual:{body.editing_parent_run_id or 'original'}"
        )
        if current not in {v[0] for v in versions} or (
            body.selected_improvement_id
            and body.selected_improvement_id not in improvements
        ):
            raise failure(
                422, "invalid_view", "Choose a retained scene from this room."
            )
        # Geometry warnings remain visible and are saved; physically invalid scenes are not silently blessed.
        reports = {
            key: validate_geometry(scene).model_dump(mode="json")
            for key, _, _, scene in versions
        }
        if any(not report["valid"] for report in reports.values()):
            raise failure(
                422,
                "invalid_scene",
                "Only validated scenes can be saved. Your current workspace is kept.",
            )
        batch = None
        project_id = body.project_id or uuid4()
        try:
            images = []
            if is_new:
                prepared = self.uploads.resolve(source["input_id"], owner)
                images = [
                    (
                        image,
                        self.uploads.read_image(
                            source["input_id"], image.image_id, owner
                        ),
                    )
                    for image in prepared.images
                ]
                batch, images = self.photos.write(images)
            with self.database.sessions.begin() as db:
                receipt = self._receipt(db, user_id, body, body_hash)
                if receipt:
                    return receipt
                now = datetime.now(UTC)
                if is_new:
                    project = Project(
                        id=project_id,
                        user_id=user_id,
                        source_run_id=body.source_run_id,
                        title=body.title,
                        revision=1,
                        snapshot={},
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(project)
                    db.flush()
                    for image in images:
                        db.add(ProjectImage(project_id=project_id, **image))
                else:
                    project = self._owned(db, project_id, user_id)
                    changed = db.execute(
                        update(Project)
                        .where(
                            Project.id == project_id,
                            Project.revision == body.expected_revision,
                        )
                        .values(revision=body.expected_revision + 1)
                    )
                    if changed.rowcount != 1:
                        raise failure(
                            409,
                            "project_changed",
                            "This room has changed. Reopen the latest saved room before saving again.",
                        )
                    if project.source_run_id != body.source_run_id:
                        raise failure(
                            409, "save_conflict", "Save changes to their original room."
                        )
                existing = {
                    v.fingerprint: v
                    for v in db.scalars(
                        select(SceneVersion).where(
                            SceneVersion.project_id == project_id
                        )
                    )
                }
                mapping, manifest = {}, []
                for key, kind, parent, scene in versions:
                    document = scene.model_dump(mode="json")
                    digest = fingerprint(
                        {
                            "kind": kind,
                            "parent": str(mapping.get(parent)),
                            "scene": document,
                        }
                    )
                    version = existing.get(digest)
                    if version is None:
                        version = SceneVersion(
                            id=uuid4(),
                            project_id=project_id,
                            kind=kind,
                            parent_id=mapping.get(parent),
                            fingerprint=digest,
                            scene_json=document,
                            validation_summary=reports[key],
                            created_at=now,
                        )
                        db.add(version)
                        db.flush()
                        existing[digest] = version
                    mapping[key] = version.id
                    manifest.append(
                        {
                            "key": key,
                            "kind": kind,
                            "parent_key": parent,
                            "version_id": str(version.id),
                        }
                    )
                project.title, project.updated_at = body.title, now
                project.snapshot = {
                    "source": source,
                    "improvements": improvements,
                    "versions": manifest,
                    "current_version_id": str(mapping[current]),
                    "comparison": {
                        "view": body.view,
                        "editing_parent_run_id": body.editing_parent_run_id,
                        "has_editable_copy": body.has_editable_copy,
                        "selected_improvement_id": body.selected_improvement_id,
                    },
                    "messages": [m.model_dump() for m in body.messages],
                }
                from roometry.agents.persistence_model import RuntimeRun
                for runtime_id in [body.source_run_id,*improvements]:
                    runtime=db.get(RuntimeRun,(user_id,runtime_id))
                    if runtime is not None:runtime.project_id=project_id
                db.flush()
                saved = SavedProject(
                    id=project.id,
                    title=project.title,
                    revision=project.revision,
                    image_count=db.scalar(
                        select(func.count())
                        .select_from(ProjectImage)
                        .where(ProjectImage.project_id == project_id)
                    ),
                    version_count=len(existing),
                    saved_at=now.isoformat(),
                )
                db.add(
                    ProjectSave(
                        id=uuid4(),
                        user_id=user_id,
                        project_id=project_id,
                        request_id=body.request_id,
                        body_hash=body_hash,
                        response_json=saved.model_dump(mode="json"),
                    )
                )
            return saved
        except IntegrityError:
            # Another identical save may have committed first. Recover it without duplicating anything.
            with self.database.sessions() as db:
                receipt = self._receipt(db, user_id, body, body_hash)
                if receipt:
                    return receipt
            raise failure(
                409,
                "save_conflict",
                "This room was already saved. Reopen its latest version.",
            ) from None
        except OSError:
            raise failure(
                503,
                "image_storage_unavailable",
                "Your photos could not be saved. Your current workspace is kept; please retry.",
            ) from None
        finally:
            if batch:
                # Never remove a possibly committed photo set after an ambiguous database failure.
                try:
                    with self.database.sessions() as db:
                        referenced = db.scalar(
                            select(ProjectImage.id)
                            .where(ProjectImage.storage_key.startswith(batch + "/"))
                            .limit(1)
                        )
                    if referenced is None:
                        self.photos.discard(batch)
                except (SQLAlchemyError, OSError):
                    logging.getLogger(__name__).warning(
                        "Private photo cleanup requires retry."
                    )

    @guarded
    def list_rooms(self,user_id,offset=0):
        with self.database.sessions() as db:
            rows=list(db.scalars(select(Project).where(Project.user_id==user_id).order_by(Project.updated_at.desc(),Project.id).offset(offset).limit(50)))
            return [{"id":str(p.id),"title":p.title,"revision":p.revision,"room_type":p.snapshot['source']['working_scene']['room']['room_type'],
                "updated_at":p.updated_at.isoformat(),"visibility":"private"} for p in rows]

    @guarded
    def rename(self,user_id,project_id,title,revision):
        with self.database.sessions.begin() as db:
            project=self._owned(db,project_id,user_id)
            changed=db.execute(update(Project).where(Project.id==project_id,Project.revision==revision).values(title=title,revision=revision+1,updated_at=datetime.now(UTC)))
            if changed.rowcount!=1:raise failure(409,'project_changed','This room has changed. Reload it before renaming.')
        return {"id":str(project_id),"title":title,"revision":revision+1}

    @guarded
    def delete_room(self,user_id,project_id,revision):
        from sqlalchemy import delete
        from roometry.agents.persistence_model import RuntimeRun,RuntimeInput
        with self.database.sessions.begin() as db:
            project=self._owned(db,project_id,user_id)
            if project.revision!=revision:raise failure(409,'project_changed','This room has changed. Reload it before deleting.')
            keys=list(db.scalars(select(ProjectImage.storage_key).where(ProjectImage.project_id==project_id)))
            db.delete(project);db.flush()
            orphaned=list(db.scalars(select(RuntimeInput).where(RuntimeInput.user_id==user_id)))
            for stored in orphaned:
                used=db.scalar(select(RuntimeRun.id).where(RuntimeRun.user_id==user_id,RuntimeRun.input_id==stored.id).limit(1))
                if used is None:
                    keys.extend(i['storage_key'] for i in stored.images);db.delete(stored)
        for batch in {key.split('/')[0] for key in keys}:
            try:
                with self.database.sessions() as db:
                    referenced=db.scalar(select(ProjectImage.id).where(ProjectImage.storage_key.startswith(batch+'/')).limit(1))
                    runtime_reference=any(any(i['storage_key'].startswith(batch+'/') for i in row.images) for row in db.scalars(select(RuntimeInput)))
                if referenced is None and not runtime_reference:self.photos.discard(batch)
            except (OSError,SQLAlchemyError):logging.getLogger(__name__).warning('Deleted room photo cleanup requires retry.')

    @guarded
    def read(self, user_id, project_id):
        self.available()
        with self.database.sessions() as db:
            project = self._owned(db, project_id, user_id)
            images = list(
                db.scalars(
                    select(ProjectImage)
                    .where(ProjectImage.project_id == project_id)
                    .order_by(ProjectImage.sort_order)
                )
            )
            versions = list(
                db.scalars(
                    select(SceneVersion)
                    .where(SceneVersion.project_id == project_id)
                    .order_by(SceneVersion.created_at, SceneVersion.id)
                )
            )
            return {
                "id": str(project.id),
                "title": project.title,
                "revision": project.revision,
                "visibility": "private",
                "snapshot": copy.deepcopy(project.snapshot),
                "versions": [
                    {
                        "id": str(v.id),
                        "kind": v.kind,
                        "parent_id": str(v.parent_id) if v.parent_id else None,
                        "scene": v.scene_json,
                        "validation": v.validation_summary,
                    }
                    for v in versions
                ],
                "images": [
                    {
                        "id": str(i.id),
                        "source_image_id": i.source_image_id,
                        "width": i.width,
                        "height": i.height,
                        "url": f"/api/projects/{project_id}/images/{i.id}",
                    }
                    for i in images
                ],
            }

    @guarded
    def image(self, user_id, project_id, image_id):
        self.available()
        with self.database.sessions() as db:
            self._owned(db, project_id, user_id)
            image = db.scalar(
                select(ProjectImage).where(
                    ProjectImage.project_id == project_id, ProjectImage.id == image_id
                )
            )
            if image is None:
                raise failure(
                    404, "image_not_found", "This saved photo is unavailable."
                )
            try:
                return self.photos.read(image.storage_key, image.sha256)
            except (ValueError, OSError):
                raise failure(
                    503,
                    "image_storage_unavailable",
                    "This saved photo is temporarily unavailable.",
                ) from None
