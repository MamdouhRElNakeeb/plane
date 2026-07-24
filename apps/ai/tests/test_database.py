# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest

from crete_plane_ai.database import SCHEMA_STATEMENTS, _vector_literal


def test_schema_enables_pgvector_and_cascades_thread_data() -> None:
    schema = "\n".join(SCHEMA_STATEMENTS)

    assert "CREATE EXTENSION IF NOT EXISTS vector" in schema
    assert "embedding vector(1536) NOT NULL" in schema
    assert schema.count("REFERENCES ai_threads(id) ON DELETE CASCADE") == 2
    assert "USING hnsw (embedding vector_cosine_ops)" in schema
    assert "'general', 'workspace', 'project', 'work_item'" in schema
    assert "authorization_version SMALLINT NOT NULL DEFAULT 0" in schema
    assert "citations JSONB NOT NULL DEFAULT '[]'::jsonb" in schema


def test_vector_literal_requires_fixed_dimensions() -> None:
    with pytest.raises(ValueError, match="1536"):
        _vector_literal([0.0])

    literal = _vector_literal([0.0] * 1536)
    assert literal.startswith("[")
    assert literal.endswith("]")
    assert literal.count(",") == 1535
