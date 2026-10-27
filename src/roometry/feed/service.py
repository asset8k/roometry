"""Publish only an owned saved version; render no source photos or private text."""

import base64
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import defer

from roometry.auth.errors import AuthFailure
from roometry.feed.model import Publication
from roometry.projects.model import Project, SceneVersion
from roometry.scenes.schemas import Opening, RoomScene, SceneObject
from roometry.scenes.validation import validate_geometry
from roometry.vision.review_render import render_views


def unavailable():
    return AuthFailure(
        404, "shared_room_not_found", "This room is no longer shared or is unavailable."
    )


def public_scene(scene: RoomScene) -> RoomScene:
    """Build a fresh renderer document, with no observations, notes or source IDs."""
    ids = {o.id: f"object_{i}" for i, o in enumerate(scene.objects, 1)}
    openings = {o.id: f"opening_{i}" for i, o in enumerate(scene.openings, 1)}
    return RoomScene(
        schema_version=1,
        room=scene.room.model_copy(deep=True),
        finishes=scene.finishes.model_copy(deep=True) if scene.finishes else None,
        assumptions=[],
        warnings=[],
        objects=[
            SceneObject(
                id=ids[o.id],
                type=o.type,
                asset_id=o.asset_id,
                position=o.position.model_copy(deep=True),
                rotation_y_rad=o.rotation_y_rad,
                dimensions_m=o.dimensions_m.model_copy(deep=True),
                color=o.color,
                material_colors=o.material_colors.model_copy(deep=True)
                if o.material_colors
                else None,
                placement=o.placement,
                wall=o.wall,
                support_object_id=ids.get(o.support_object_id),
                support_surface_id=o.support_surface_id,
                opening_id=openings.get(o.opening_id),
                fixed=True,
                locked=True,
                provenance="user_added",
                measurements_source="estimated",
            )
            for o in scene.objects
        ],
        openings=[
            Opening(
                id=openings[o.id],
                type=o.type,
                wall=o.wall,
                offset_m=o.offset_m,
                width_m=o.width_m,
                height_m=o.height_m,
                sill_height_m=o.sill_height_m,
                swing=o.swing,
                hinge=o.hinge,
                provenance="user_added",
                measurements_source="estimated",
            )
            for o in scene.openings
        ],
    )


def preview(scene):
    try:
        result = render_views(scene, annotate=False, view_ids=("cutaway_nw",))[0].image
        if len(result) > 2 * 1024 * 1024:
            raise ValueError("Preview budget exceeded.")
    except ValueError:
        raise AuthFailure(
            503,
            "preview_unavailable",
            "This room preview could not be prepared. Please retry.",
        ) from None
    return result


class FeedService:
    def __init__(self, database):
        self.database = database

    def available(self):
        if self.database is None:
            raise AuthFailure(
                503, "feed_unavailable", "Explore is temporarily unavailable."
            )

    @contextmanager
    def write(self):
        with self.database.sessions.begin() as db:
            if self.database.engine.dialect.name == "sqlite":
                db.execute(text("BEGIN IMMEDIATE"))
            yield db

    @staticmethod
    def entries(project):
        comparison = project.snapshot["comparison"]
        editable = (
            comparison.get("has_editable_copy", False)
            or comparison["view"] == "editing"
        )
        return [
            v
            for v in project.snapshot["versions"]
            if not v["key"].startswith("context:")
            and (not v["key"].startswith("manual:") or editable)
        ]

    @staticmethod
    def owned(db, user_id, project_id):
        item = db.scalar(
            select(Project)
            .where(Project.id == project_id, Project.user_id == user_id)
            .with_for_update()
        )
        if item is None:
            raise AuthFailure(
                404, "project_not_found", "This saved room is unavailable."
            )
        return item

    @staticmethod
    def version(db, project, version_id):
        allowed = {v["version_id"] for v in FeedService.entries(project)}
        item = db.get(SceneVersion, version_id)
        if (
            item is None
            or item.project_id != project.id
            or str(version_id) not in allowed
        ):
            raise AuthFailure(
                404, "version_not_found", "Choose a layout from this saved room."
            )
        scene = RoomScene.model_validate(item.scene_json)
        if not validate_geometry(scene).valid:
            raise AuthFailure(
                409, "invalid_shared_scene", "Save a validated layout before sharing."
            )
        return public_scene(scene)

    @staticmethod
    def publication(row):
        return (
            None
            if row is None
            else {
                "active": row.active,
                "id": str(row.public_id),
                "revision": row.revision,
                "version_id": str(row.version_id) if row.version_id else None,
                "title": row.title,
                "summary": row.summary,
            }
        )

    def options(self, user_id, request):
        self.available()
        with self.database.sessions() as db:
            project = db.scalar(
                select(Project).where(
                    Project.user_id == user_id,
                    Project.source_run_id == request.source_run_id,
                )
            )
            if project is None:
                return None
            versions, selected = [], None
            counters = {"base": 0, "optimized": 0, "refined": 0, "manual": 0}
            for entry in self.entries(project):
                version = db.get(SceneVersion, UUID(entry["version_id"]))
                counters[version.kind] += 1
                label = {
                    "base": "Original",
                    "optimized": "Improved",
                    "refined": "Chat refinement",
                    "manual": "Your layout",
                }[version.kind]
                if counters[version.kind] > 1:
                    label += f" {counters[version.kind]}"
                versions.append(
                    {
                        "id": str(version.id),
                        "label": label,
                        "preview_url": f"/api/projects/{project.id}/versions/{version.id}/preview",
                    }
                )
                if (
                    selected is None
                    and RoomScene.model_validate(version.scene_json) == request.scene
                ):
                    selected = str(version.id)
            return {
                "project_id": str(project.id),
                "revision": project.revision,
                "title": project.title,
                "versions": versions,
                "selected_version_id": selected,
                "publication": self.publication(db.get(Publication, project.id)),
            }

    def private_preview(self, user_id, project_id, version_id):
        self.available()
        with self.database.sessions() as db:
            project = self.owned(db, user_id, project_id)
            scene = self.version(db, project, version_id)
        return preview(scene)

    def publish(self, user_id, project_id, body):
        self.available()
        # Rendering is outside the write transaction. Recheck ownership/revision
        # after it finishes, so concurrent save/delete/unpublish cannot be lost.
        with self.database.sessions() as db:
            project = self.owned(db, user_id, project_id)
            if project.revision != body.expected_revision:
                raise AuthFailure(
                    409,
                    "project_changed",
                    "Your saved room changed. Reopen sharing and choose its latest layout.",
                )
            scene = self.version(db, project, body.version_id)
        image = preview(scene)
        with self.write() as db:
            project = self.owned(db, user_id, project_id)
            if project.revision != body.expected_revision:
                raise AuthFailure(
                    409,
                    "project_changed",
                    "Your saved room changed. Reopen sharing and choose its latest layout.",
                )
            # The selected saved version must still be in the current manifest.
            self.version(db, project, body.version_id)
            row = db.get(Publication, project_id)
            if (
                row
                and row.active
                and (row.version_id, row.title, row.summary)
                == (body.version_id, body.title, body.summary)
            ):
                return self.publication(row)
            if (row.revision if row else 0) != body.expected_publication_revision:
                raise AuthFailure(
                    409,
                    "sharing_changed",
                    "Sharing changed in another tab. Reopen sharing before updating it.",
                )
            if row is None:
                row = Publication(project_id=project_id, revision=0)
                db.add(row)
            if not row.active:
                row.public_id = uuid4()
            row.revision += 1
            row.active, row.version_id = True, body.version_id
            row.title, row.summary = body.title, body.summary
            row.scene, row.preview, row.published_at = (
                scene.model_dump(mode="json"),
                image,
                datetime.now(UTC),
            )
            db.flush()
            return self.publication(row)

    def unpublish(self, user_id, project_id, body):
        self.available()
        with self.write() as db:
            self.owned(db, user_id, project_id)
            row = db.get(Publication, project_id)
            if row is None or not row.active:
                return
            if (
                row.public_id != body.public_id
                or row.revision != body.expected_publication_revision
            ):
                raise AuthFailure(
                    409,
                    "sharing_changed",
                    "Sharing changed in another tab. Reopen sharing before removing it.",
                )
            row.active, row.scene, row.preview, row.version_id = False, None, None, None
            row.title, row.summary = "", ""
            row.revision += 1

    @staticmethod
    def card(row):
        return {
            "id": str(row.public_id),
            "title": row.title,
            "summary": row.summary,
            "room_type": row.scene["room"]["room_type"],
            "object_count": len(row.scene["objects"]),
            "published_at": row.published_at.replace(tzinfo=UTC),
            "preview_url": f"/api/feed/{row.public_id}/preview",
        }

    def list(self, cursor=None):
        self.available()
        query = (
            select(Publication)
            .options(defer(Publication.preview))
            .where(Publication.active.is_(True))
        )
        if cursor:
            try:
                stamp, key = json.loads(
                    base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
                )
                stamp, key = datetime.fromisoformat(stamp), UUID(key)
                if stamp.tzinfo is None:
                    raise ValueError()
            except (ValueError, TypeError, KeyError, UnicodeError):
                raise AuthFailure(
                    422, "invalid_cursor", "Reload Explore to continue browsing."
                ) from None
            query = query.where(
                or_(
                    Publication.published_at < stamp,
                    and_(
                        Publication.published_at == stamp, Publication.public_id < key
                    ),
                )
            )
        with self.database.sessions() as db:
            rows = list(
                db.scalars(
                    query.order_by(
                        Publication.published_at.desc(), Publication.public_id.desc()
                    ).limit(13)
                )
            )
            more, rows = len(rows) > 12, rows[:12]
            cursor = (
                base64.urlsafe_b64encode(
                    json.dumps(
                        [
                            rows[-1].published_at.replace(tzinfo=UTC).isoformat(),
                            str(rows[-1].public_id),
                        ]
                    ).encode()
                )
                .decode()
                .rstrip("=")
                if more
                else None
            )
            return {"items": [self.card(row) for row in rows], "next_cursor": cursor}

    def read(self, public_id, image=False):
        self.available()
        with self.database.sessions() as db:
            row = db.scalar(
                select(Publication).where(
                    Publication.public_id == public_id, Publication.active.is_(True)
                )
            )
            if row is None:
                raise unavailable()
            return row.preview if image else {**self.card(row), "scene": row.scene}
