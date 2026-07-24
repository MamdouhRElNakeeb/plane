# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ThreadCreate(StrictModel):
    user_id: UUID
    authorization_version: Literal[1]
    title: str | None = Field(default=None, max_length=300)
    context_type: Literal["general", "workspace", "project", "work_item"] | None = None
    project_id: UUID | None = None
    issue_id: UUID | None = None
    authorized_project_ids: list[UUID] = Field(default_factory=list, max_length=500)
    restricted_project_ids: list[UUID] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def validate_context_ids(self) -> "ThreadCreate":
        if self.context_type == "project" and self.project_id is None:
            raise ValueError("project_id is required for project context")
        if self.context_type == "work_item" and (self.project_id is None or self.issue_id is None):
            raise ValueError("project_id and issue_id are required for issue context")
        if not set(self.restricted_project_ids).issubset(self.authorized_project_ids):
            raise ValueError("restricted projects must be included in authorized projects")
        return self


class RetrieveRequest(StrictModel):
    workspace_id: UUID
    user_id: UUID
    query: str = Field(min_length=1, max_length=4000)
    allowed_project_ids: list[UUID] = Field(max_length=500)
    project_id: UUID | None = None
    limit: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def validate_project_scope(self) -> "RetrieveRequest":
        if self.project_id is not None and self.project_id not in self.allowed_project_ids:
            raise ValueError("project_id must be included in allowed_project_ids")
        return self


class IndexRequest(StrictModel):
    object_type: Literal["issue"]
    object_id: UUID
    workspace_id: UUID
    project_id: UUID
    title: str = Field(max_length=2000)
    content: str = Field(max_length=100_000)
    updated_at: datetime


class IndexDeleteRequest(StrictModel):
    object_type: Literal["issue"]
    object_id: UUID
    workspace_id: UUID


class ContextItem(StrictModel):
    object_type: Literal["issue"]
    object_id: UUID
    project_id: UUID
    title: str = Field(max_length=2000)
    content: str = Field(max_length=100_000)


class ChatRequest(StrictModel):
    thread_id: UUID
    workspace_id: UUID
    user_id: UUID
    prompt: str = Field(min_length=1, max_length=32_000)
    context_type: Literal["general", "workspace", "project", "work_item"]
    project_id: UUID | None = None
    issue_id: UUID | None = None
    context_items: list[ContextItem] = Field(default_factory=list, max_length=30)
    model: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        validation_alias=AliasChoices("model", "current_model"),
    )

    @model_validator(mode="after")
    def validate_context(self) -> "ChatRequest":
        if self.context_type == "project" and self.project_id is None:
            raise ValueError("project_id is required for project context")
        if self.context_type == "work_item" and (self.project_id is None or self.issue_id is None):
            raise ValueError("project_id and issue_id are required for issue context")
        if sum(len(item.title) + len(item.content) for item in self.context_items) > 200_000:
            raise ValueError("context_items exceed the combined size limit")
        allowed_projects = {item.project_id for item in self.context_items}
        if self.project_id is not None and allowed_projects and allowed_projects != {self.project_id}:
            raise ValueError("context_items must belong to the requested project")
        return self


class ActionCompleteRequest(StrictModel):
    workspace_id: UUID
    user_id: UUID
    result: dict[str, Any]

    @model_validator(mode="after")
    def validate_result_size(self) -> "ActionCompleteRequest":
        if len(json.dumps(self.result, separators=(",", ":"), default=str)) > 65_536:
            raise ValueError("result exceeds the size limit")
        return self


ActionName = Literal["create_comment", "edit_issue_description", "create_subtask"]


class CreateCommentArguments(StrictModel):
    project_id: UUID
    issue_id: UUID
    comment_html: str = Field(min_length=1, max_length=32_000)


class EditIssueDescriptionArguments(StrictModel):
    project_id: UUID
    issue_id: UUID
    description_html: str = Field(max_length=100_000)


class CreateSubtaskArguments(StrictModel):
    project_id: UUID
    parent_issue_id: UUID
    name: str = Field(min_length=1, max_length=255)
    description_html: str = Field(max_length=100_000)
