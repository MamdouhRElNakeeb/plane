# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from crete_plane_ai.config import Settings

SCHEMA_STATEMENTS = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    """
    CREATE TABLE IF NOT EXISTS ai_threads (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        workspace_id UUID NOT NULL,
        user_id UUID NOT NULL,
        authorization_version SMALLINT NOT NULL DEFAULT 0,
        title VARCHAR(300),
        context_type VARCHAR(20),
        project_id UUID,
        issue_id UUID,
        authorized_project_ids UUID[] NOT NULL DEFAULT '{}',
        restricted_project_ids UUID[] NOT NULL DEFAULT '{}',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT ai_threads_context_type_check
            CHECK (
                context_type IS NULL
                OR context_type IN ('general', 'workspace', 'project', 'work_item')
            )
    )
    """,
    """
    ALTER TABLE ai_threads DROP CONSTRAINT IF EXISTS ai_threads_context_type_check
    """,
    """
    ALTER TABLE ai_threads
    ADD CONSTRAINT ai_threads_context_type_check
    CHECK (
        context_type IS NULL
        OR context_type IN ('general', 'workspace', 'project', 'work_item')
    )
    """,
    """
    ALTER TABLE ai_threads
    ADD COLUMN IF NOT EXISTS authorized_project_ids UUID[] NOT NULL DEFAULT '{}'
    """,
    """
    ALTER TABLE ai_threads
    ADD COLUMN IF NOT EXISTS restricted_project_ids UUID[] NOT NULL DEFAULT '{}'
    """,
    """
    ALTER TABLE ai_threads
    ADD COLUMN IF NOT EXISTS authorization_version SMALLINT NOT NULL DEFAULT 0
    """,
    """
    CREATE INDEX IF NOT EXISTS ai_threads_owner_updated_idx
    ON ai_threads (workspace_id, user_id, updated_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_messages (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        thread_id UUID NOT NULL REFERENCES ai_threads(id) ON DELETE CASCADE,
        role VARCHAR(20) NOT NULL CHECK (role IN ('user', 'assistant')),
        content TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ai_messages_thread_created_idx
    ON ai_messages (thread_id, created_at ASC)
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_action_proposals (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        thread_id UUID NOT NULL REFERENCES ai_threads(id) ON DELETE CASCADE,
        message_id UUID REFERENCES ai_messages(id) ON DELETE SET NULL,
        workspace_id UUID NOT NULL,
        user_id UUID NOT NULL,
        action_name VARCHAR(64) NOT NULL CHECK (
            action_name IN ('create_comment', 'edit_issue_description', 'create_subtask')
        ),
        arguments JSONB NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed')),
        result JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    ALTER TABLE ai_action_proposals
    ADD COLUMN IF NOT EXISTS message_id UUID REFERENCES ai_messages(id) ON DELETE SET NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS ai_action_proposals_owner_idx
    ON ai_action_proposals (workspace_id, user_id, status, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_request_nonces (
        nonce UUID PRIMARY KEY,
        expires_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ai_request_nonces_expires_idx
    ON ai_request_nonces (expires_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_search_documents (
        object_id UUID PRIMARY KEY,
        object_type VARCHAR(32) NOT NULL CHECK (object_type = 'issue'),
        workspace_id UUID NOT NULL,
        project_id UUID NOT NULL,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        source_updated_at TIMESTAMPTZ NOT NULL,
        embedding vector(1536) NOT NULL,
        indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ai_search_documents_scope_idx
    ON ai_search_documents (workspace_id, project_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ai_search_documents_embedding_idx
    ON ai_search_documents USING hnsw (embedding vector_cosine_ops)
    """,
)


def _vector_literal(embedding: Sequence[float]) -> str:
    if len(embedding) != 1536:
        raise ValueError("embedding must have exactly 1536 dimensions")
    return "[" + ",".join(str(float(value)) for value in embedding) + "]"


class Repository:
    def __init__(self, settings: Settings) -> None:
        self.pool = AsyncConnectionPool(
            conninfo=settings.ai_database_url.get_secret_value(),
            min_size=settings.ai_database_pool_min_size,
            max_size=settings.ai_database_pool_max_size,
            open=False,
            kwargs={"row_factory": dict_row},
        )

    async def open(self) -> None:
        await self.pool.open()
        await self.pool.wait()
        async with self.pool.connection() as connection:
            async with connection.transaction():
                await connection.execute("SELECT pg_advisory_xact_lock(182918341503821)")
                for statement in SCHEMA_STATEMENTS:
                    await connection.execute(statement)

    async def close(self) -> None:
        await self.pool.close()

    async def healthcheck(self) -> None:
        async with self.pool.connection() as connection:
            await connection.execute("SELECT 1")

    async def claim_request_nonce(self, nonce: UUID, expires_at: int) -> bool:
        async with self.pool.connection() as connection:
            async with connection.transaction():
                await connection.execute("DELETE FROM ai_request_nonces WHERE expires_at < NOW()")
                cursor = await connection.execute(
                    """
                    INSERT INTO ai_request_nonces (nonce, expires_at)
                    VALUES (%s, TO_TIMESTAMP(%s))
                    ON CONFLICT (nonce) DO NOTHING
                    RETURNING nonce
                    """,
                    (nonce, expires_at),
                )
                return await cursor.fetchone() is not None

    async def list_threads(self, workspace_id: UUID, user_id: UUID) -> list[dict[str, Any]]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT id, workspace_id, user_id, authorization_version, title, context_type, project_id, issue_id,
                       authorized_project_ids, restricted_project_ids,
                       created_at, updated_at
                FROM ai_threads
                WHERE workspace_id = %s AND user_id = %s
                ORDER BY updated_at DESC
                LIMIT 200
                """,
                (workspace_id, user_id),
            )
            return list(await cursor.fetchall())

    async def create_thread(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        authorization_version: int,
        title: str | None,
        context_type: str | None,
        project_id: UUID | None,
        issue_id: UUID | None,
        authorized_project_ids: list[UUID],
        restricted_project_ids: list[UUID],
    ) -> dict[str, Any]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO ai_threads (
                    workspace_id, user_id, authorization_version, title, context_type, project_id, issue_id,
                    authorized_project_ids, restricted_project_ids
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, workspace_id, user_id, authorization_version, title, context_type, project_id, issue_id,
                          authorized_project_ids, restricted_project_ids,
                          created_at, updated_at
                """,
                (
                    workspace_id,
                    user_id,
                    authorization_version,
                    title,
                    context_type,
                    project_id,
                    issue_id,
                    authorized_project_ids,
                    restricted_project_ids,
                ),
            )
            return dict(await cursor.fetchone())

    async def get_thread(self, workspace_id: UUID, thread_id: UUID, user_id: UUID) -> dict[str, Any] | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT id, workspace_id, user_id, authorization_version, title, context_type, project_id, issue_id,
                       authorized_project_ids, restricted_project_ids,
                       created_at, updated_at
                FROM ai_threads
                WHERE id = %s AND workspace_id = %s AND user_id = %s
                """,
                (thread_id, workspace_id, user_id),
            )
            thread = await cursor.fetchone()
            if thread is None:
                return None
            messages_cursor = await connection.execute(
                """
                SELECT id, role, content, created_at
                FROM (
                    SELECT id, role, content, created_at
                    FROM ai_messages
                    WHERE thread_id = %s
                    ORDER BY created_at DESC
                    LIMIT 200
                ) AS recent_messages
                ORDER BY created_at ASC
                """,
                (thread_id,),
            )
            actions_cursor = await connection.execute(
                """
                SELECT action.id, action.message_id, action.action_name, action.arguments,
                       action.status, action.result, action.created_at, action.completed_at
                FROM ai_action_proposals AS action
                INNER JOIN ai_threads AS thread ON thread.id = action.thread_id
                WHERE action.thread_id = %s
                  AND action.workspace_id = %s
                  AND action.user_id = %s
                  AND thread.workspace_id = %s
                  AND thread.user_id = %s
                ORDER BY action.created_at ASC
                LIMIT 100
                """,
                (thread_id, workspace_id, user_id, workspace_id, user_id),
            )
            result = dict(thread)
            result["messages"] = list(await messages_cursor.fetchall())
            result["proposals"] = list(await actions_cursor.fetchall())
            return result

    async def delete_thread(self, workspace_id: UUID, thread_id: UUID, user_id: UUID) -> bool:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                DELETE FROM ai_threads
                WHERE id = %s AND workspace_id = %s AND user_id = %s
                """,
                (thread_id, workspace_id, user_id),
            )
            return cursor.rowcount == 1

    async def thread_exists(self, workspace_id: UUID, thread_id: UUID, user_id: UUID) -> bool:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT EXISTS(
                    SELECT 1 FROM ai_threads
                    WHERE id = %s AND workspace_id = %s AND user_id = %s
                ) AS found
                """,
                (thread_id, workspace_id, user_id),
            )
            row = await cursor.fetchone()
            return bool(row["found"])

    async def add_message(self, thread_id: UUID, role: str, content: str) -> dict[str, Any]:
        async with self.pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    INSERT INTO ai_messages (thread_id, role, content)
                    VALUES (%s, %s, %s)
                    RETURNING id, role, content, created_at
                    """,
                    (thread_id, role, content),
                )
                if role == "user":
                    await connection.execute(
                        """
                        UPDATE ai_threads
                        SET title = COALESCE(NULLIF(title, ''), LEFT(%s, 80)),
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (content, thread_id),
                    )
                else:
                    await connection.execute(
                        "UPDATE ai_threads SET updated_at = NOW() WHERE id = %s",
                        (thread_id,),
                    )
                return dict(await cursor.fetchone())

    async def get_history(self, thread_id: UUID, limit: int) -> list[dict[str, Any]]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT role, content
                FROM (
                    SELECT role, content, created_at
                    FROM ai_messages
                    WHERE thread_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                ) AS recent_messages
                ORDER BY created_at ASC
                """,
                (thread_id, limit),
            )
            return list(await cursor.fetchall())

    async def create_action(
        self,
        *,
        thread_id: UUID,
        message_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        action_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO ai_action_proposals (
                    thread_id, message_id, workspace_id, user_id, action_name, arguments
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, thread_id, message_id, workspace_id, user_id, action_name, arguments,
                          status, created_at, updated_at
                """,
                (thread_id, message_id, workspace_id, user_id, action_name, Jsonb(arguments)),
            )
            return dict(await cursor.fetchone())

    async def get_pending_action(self, action_id: UUID, workspace_id: UUID, user_id: UUID) -> dict[str, Any] | None:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT action.id, action.thread_id, action.workspace_id, action.user_id,
                       action.action_name, action.arguments,
                       action.status, action.created_at, action.updated_at
                FROM ai_action_proposals AS action
                INNER JOIN ai_threads AS thread ON thread.id = action.thread_id
                WHERE action.id = %s
                  AND action.workspace_id = %s
                  AND action.user_id = %s
                  AND action.status = 'pending'
                  AND thread.workspace_id = %s
                  AND thread.user_id = %s
                """,
                (action_id, workspace_id, user_id, workspace_id, user_id),
            )
            row = await cursor.fetchone()
            return dict(row) if row is not None else None

    async def complete_action(
        self,
        action_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        async with self.pool.connection() as connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    SELECT action.id, action.status
                    FROM ai_action_proposals AS action
                    WHERE action.id = %s
                      AND action.workspace_id = %s
                      AND action.user_id = %s
                      AND EXISTS (
                          SELECT 1
                          FROM ai_threads AS thread
                          WHERE thread.id = action.thread_id
                            AND thread.workspace_id = %s
                            AND thread.user_id = %s
                      )
                    FOR UPDATE
                    """,
                    (action_id, workspace_id, user_id, workspace_id, user_id),
                )
                action = await cursor.fetchone()
                if action is None:
                    return None
                if action["status"] == "pending":
                    await connection.execute(
                        """
                        UPDATE ai_action_proposals
                        SET status = 'completed', result = %s, completed_at = NOW(), updated_at = NOW()
                        WHERE id = %s
                        """,
                        (Jsonb(result), action_id),
                    )
                result_cursor = await connection.execute(
                    """
                    SELECT id, thread_id, workspace_id, user_id, action_name, arguments,
                           status, result, created_at, completed_at, updated_at
                    FROM ai_action_proposals
                    WHERE id = %s
                    """,
                    (action_id,),
                )
                return dict(await result_cursor.fetchone())

    async def retrieve(
        self,
        *,
        workspace_id: UUID,
        allowed_project_ids: list[UUID],
        project_id: UUID | None,
        embedding: Sequence[float],
        limit: int,
    ) -> list[dict[str, Any]]:
        if not allowed_project_ids:
            return []
        vector = _vector_literal(embedding)
        parameters: list[Any] = [vector, workspace_id, allowed_project_ids]
        project_clause = ""
        if project_id is not None:
            project_clause = "AND project_id = %s"
            parameters.append(project_id)
        parameters.append(limit)
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                f"""
                SELECT object_id, 1 - (embedding <=> %s::vector) AS score
                FROM ai_search_documents
                WHERE workspace_id = %s
                  AND project_id = ANY(%s::uuid[])
                  {project_clause}
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (*parameters[:-1], vector, parameters[-1]),
            )
            return list(await cursor.fetchall())

    async def upsert_document(
        self,
        *,
        object_type: str,
        object_id: UUID,
        workspace_id: UUID,
        project_id: UUID,
        title: str,
        content: str,
        updated_at: Any,
        embedding: Sequence[float],
    ) -> bool:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO ai_search_documents (
                    object_type, object_id, workspace_id, project_id, title, content,
                    source_updated_at, embedding
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)
                ON CONFLICT (object_id) DO UPDATE SET
                    object_type = EXCLUDED.object_type,
                    workspace_id = EXCLUDED.workspace_id,
                    project_id = EXCLUDED.project_id,
                    title = EXCLUDED.title,
                    content = EXCLUDED.content,
                    source_updated_at = EXCLUDED.source_updated_at,
                    embedding = EXCLUDED.embedding,
                    indexed_at = NOW()
                WHERE EXCLUDED.source_updated_at >= ai_search_documents.source_updated_at
                """,
                (
                    object_type,
                    object_id,
                    workspace_id,
                    project_id,
                    title,
                    content,
                    updated_at,
                    _vector_literal(embedding),
                ),
            )
            return cursor.rowcount == 1

    async def delete_document(self, object_type: str, object_id: UUID, workspace_id: UUID) -> bool:
        async with self.pool.connection() as connection:
            cursor = await connection.execute(
                """
                DELETE FROM ai_search_documents
                WHERE object_type = %s AND object_id = %s AND workspace_id = %s
                """,
                (object_type, object_id, workspace_id),
            )
            return cursor.rowcount == 1
