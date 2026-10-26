"""SQLAlchemy implementation of LangGraph's checkpoint + pending-write contract."""

import anyio
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    CheckpointTuple,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import delete, select

from roometry.agents.checkpointing import RunUnavailable
from roometry.agents.persistence_model import (
    GraphCheckpoint,
    GraphWrite,
    RuntimeInput,
    RuntimeRun,
)


class SqlCheckpoints(BaseCheckpointSaver):
    def __init__(self, database, user_id, run_id, vision=None):
        super().__init__(serde=JsonPlusSerializer(allowed_msgpack_modules=[]))
        self.database, self.user_id, self.run_id, self.vision = (
            database,
            user_id,
            run_id,
            vision,
        )

    def _config(self, namespace, checkpoint_id):
        return {
            "configurable": {
                "thread_id": self.run_id,
                "checkpoint_ns": namespace,
                "checkpoint_id": checkpoint_id,
            }
        }

    def _run(self, db, config):
        if config["configurable"]["thread_id"] != self.run_id:
            raise RunUnavailable()
        run = db.get(RuntimeRun, (self.user_id, self.run_id))
        if run is None:
            raise RunUnavailable()
        return run

    def get_tuple(self, config):
        namespace = config["configurable"].get("checkpoint_ns", "")
        with self.database.sessions() as db:
            self._run(db, config)
            query = select(GraphCheckpoint).where(
                GraphCheckpoint.user_id == self.user_id,
                GraphCheckpoint.run_id == self.run_id,
                GraphCheckpoint.namespace == namespace,
            )
            checkpoint_id = config["configurable"].get("checkpoint_id")
            if checkpoint_id:
                query = query.where(GraphCheckpoint.checkpoint_id == checkpoint_id)
            record = db.scalar(
                query.order_by(GraphCheckpoint.checkpoint_id.desc()).limit(1)
            )
            if record is None:
                return None
            writes = list(
                db.scalars(
                    select(GraphWrite)
                    .where(
                        GraphWrite.user_id == self.user_id,
                        GraphWrite.run_id == self.run_id,
                        GraphWrite.namespace == namespace,
                        GraphWrite.checkpoint_id == record.checkpoint_id,
                    )
                    .order_by(GraphWrite.task_path, GraphWrite.task_id, GraphWrite.idx)
                )
            )
            return CheckpointTuple(
                config=self._config(namespace, record.checkpoint_id),
                checkpoint=self.serde.loads_typed((record.type, record.payload)),
                metadata=record.metadata_json,
                parent_config=self._config(namespace, record.parent_id)
                if record.parent_id
                else None,
                pending_writes=[
                    (w.task_id, w.channel, self.serde.loads_typed((w.type, w.payload)))
                    for w in writes
                ],
            )

    def put(self, config, checkpoint, metadata, new_versions):
        namespace = config["configurable"].get("checkpoint_ns", "")
        type_, payload = self.serde.dumps_typed(checkpoint)
        with self.database.sessions.begin() as db:
            run = self._run(db, config)
            db.merge(
                GraphCheckpoint(
                    user_id=self.user_id,
                    run_id=self.run_id,
                    namespace=namespace,
                    checkpoint_id=checkpoint["id"],
                    parent_id=config["configurable"].get("checkpoint_id"),
                    type=type_,
                    payload=payload,
                    metadata_json=get_checkpoint_metadata(config, metadata),
                )
            )
            values = checkpoint.get("channel_values", {})
            state = values.get("run", values)
            if "status" in state:
                run.status = state["status"]
            if state.get("usage"):
                run.usage = state["usage"]
            if self.vision:
                cached = self.vision(run.input_id)
                if cached is not None:
                    db.get(RuntimeInput, (self.user_id, run.input_id)).vision = cached
        return self._config(namespace, checkpoint["id"])

    def put_writes(self, config, writes, task_id, task_path=""):
        namespace = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]
        with self.database.sessions.begin() as db:
            self._run(db, config)
            for index, (channel, value) in enumerate(writes):
                idx = WRITES_IDX_MAP.get(channel, index)
                key = (
                    self.user_id,
                    self.run_id,
                    namespace,
                    checkpoint_id,
                    task_id,
                    idx,
                )
                existing = db.get(GraphWrite, key)
                if existing is not None and idx >= 0:
                    continue
                type_, payload = self.serde.dumps_typed(value)
                db.merge(
                    GraphWrite(
                        user_id=self.user_id,
                        run_id=self.run_id,
                        namespace=namespace,
                        checkpoint_id=checkpoint_id,
                        task_id=task_id,
                        idx=idx,
                        channel=channel,
                        type=type_,
                        payload=payload,
                        task_path=task_path,
                    )
                )

    def list(self, config, *, filter=None, before=None, limit=None):
        with self.database.sessions() as db:
            query = select(GraphCheckpoint).where(
                GraphCheckpoint.user_id == self.user_id,
                GraphCheckpoint.run_id == self.run_id,
            )
            if config:
                query = query.where(
                    GraphCheckpoint.namespace
                    == config["configurable"].get("checkpoint_ns", "")
                )
            if before:
                query = query.where(
                    GraphCheckpoint.checkpoint_id
                    < before["configurable"]["checkpoint_id"]
                )
            rows = [
                (r.namespace, r.checkpoint_id, r.metadata_json)
                for r in db.scalars(
                    query.order_by(GraphCheckpoint.checkpoint_id.desc())
                )
            ]
        count = 0
        for namespace, checkpoint_id, metadata in rows:
            if filter and any(
                metadata.get(key) != value for key, value in filter.items()
            ):
                continue
            if limit is not None and count >= limit:
                break
            yield self.get_tuple(self._config(namespace, checkpoint_id))
            count += 1

    def delete_thread(self, thread_id):
        if thread_id != self.run_id:
            raise RunUnavailable()
        with self.database.sessions.begin() as db:
            for model in (GraphWrite, GraphCheckpoint):
                db.execute(
                    delete(model).where(
                        model.user_id == self.user_id, model.run_id == self.run_id
                    )
                )

    async def aget_tuple(self, config):
        return await anyio.to_thread.run_sync(self.get_tuple, config)

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await anyio.to_thread.run_sync(
            self.put, config, checkpoint, metadata, new_versions
        )

    async def aput_writes(self, config, writes, task_id, task_path=""):
        await anyio.to_thread.run_sync(
            self.put_writes, config, writes, task_id, task_path
        )

    async def alist(self, config, *, filter=None, before=None, limit=None):
        rows = await anyio.to_thread.run_sync(
            lambda: list(self.list(config, filter=filter, before=before, limit=limit))
        )
        for row in rows:
            yield row

    async def adelete_thread(self, thread_id):
        await anyio.to_thread.run_sync(self.delete_thread, thread_id)
