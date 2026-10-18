# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import json
from datetime import date, datetime
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


class ReportCatalogItem(StrictModel):
    id: UUID
    kind: Literal["project", "state", "cycle", "module", "label", "issue_type", "assignee"]
    name: str = Field(min_length=1, max_length=255)
    project_id: UUID | None = None
    identifier: str | None = Field(default=None, max_length=32)
    group: str | None = Field(default=None, max_length=32)
    is_current: bool = False


class ReportFilters(StrictModel):
    project_ids: list[UUID] = Field(max_length=20)
    state_groups: list[Literal["backlog", "unstarted", "started", "completed", "cancelled"]] = Field(max_length=5)
    state_ids: list[UUID] = Field(max_length=20)
    priorities: list[Literal["urgent", "high", "medium", "low", "none"]] = Field(max_length=5)
    cycle_ids: list[UUID] = Field(max_length=20)
    module_ids: list[UUID] = Field(max_length=20)
    label_ids: list[UUID] = Field(max_length=20)
    issue_type_ids: list[UUID] = Field(max_length=20)
    assignee_ids: list[UUID] = Field(max_length=20)
    current_cycle: bool
    due: Literal["any", "overdue", "due_today", "no_due_date"]
    name_contains: str | None = Field(min_length=1, max_length=200)
    created_after: date | None
    created_before: date | None
    updated_after: date | None


class ReportPlan(StrictModel):
    mode: Literal["chat", "report"]
    filters: ReportFilters
    group_by: Literal[
        "none",
        "state",
        "state_group",
        "assignee",
        "priority",
        "project",
        "cycle",
        "module",
        "label",
        "issue_type",
    ]
    include_items: bool
    sort: Literal["updated_desc", "created_desc", "due_asc", "priority_desc"]
    limit: int = Field(ge=1, le=25)


class ReportPlanRequest(StrictModel):
    prompt: str = Field(min_length=1, max_length=12_000)
    context_type: Literal["general", "workspace", "project", "work_item"]
    current_date: date
    catalog: list[ReportCatalogItem] = Field(default_factory=list, max_length=500)


class ChatRequest(StrictModel):
    thread_id: UUID
    workspace_id: UUID
    user_id: UUID
    prompt: str = Field(min_length=1, max_length=32_000)
    context_type: Literal["general", "workspace", "project", "work_item"]
    project_id: UUID | None = None
    issue_id: UUID | None = None
    context_items: list[ContextItem] = Field(default_factory=list, max_length=30)
    catalog: list[ReportCatalogItem] = Field(default_factory=list, max_length=500)
    report_result: dict[str, Any] | None = None
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
        if self.report_result is not None:
            report_size = len(json.dumps(self.report_result, separators=(",", ":"), default=str))
            if report_size > 100_000:
                raise ValueError("report_result exceeds the size limit")
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


ActionName = Literal[
    "create_comment",
    "edit_issue_description",
    "create_subtask",
    "create_issue",
    "update_issue",
    "create_module",
    "create_cycle",
    "bulk_update_issues",
    "archive_issues",
]


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


class IssueFields(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description_html: str | None = Field(default=None, max_length=100_000)
    state_id: UUID | None = None
    priority: Literal["urgent", "high", "medium", "low", "none"] | None = None
    start_date: date | None = None
    target_date: date | None = None
    assignee_ids: list[UUID] | None = Field(default=None, max_length=20)
    label_ids: list[UUID] | None = Field(default=None, max_length=20)
    cycle_id: UUID | None = None
    module_ids: list[UUID] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def validate_dates(self) -> "IssueFields":
        if self.start_date is not None and self.target_date is not None and self.start_date > self.target_date:
            raise ValueError("start_date cannot exceed target_date")
        return self


class CreateIssueArguments(IssueFields):
    project_id: UUID
    name: str = Field(min_length=1, max_length=255)


class UpdateIssueArguments(IssueFields):
    project_id: UUID
    issue_id: UUID
    fields_to_update: list[
        Literal[
            "name",
            "description_html",
            "state_id",
            "priority",
            "start_date",
            "target_date",
            "assignee_ids",
            "label_ids",
            "cycle_id",
            "module_ids",
        ]
    ] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_changes(self) -> "UpdateIssueArguments":
        if len(set(self.fields_to_update)) != len(self.fields_to_update):
            raise ValueError("fields_to_update must not contain duplicates")
        return self


class CreateModuleArguments(StrictModel):
    project_id: UUID
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=10_000)
    start_date: date | None = None
    target_date: date | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> "CreateModuleArguments":
        if self.start_date is not None and self.target_date is not None and self.start_date > self.target_date:
            raise ValueError("start_date cannot exceed target_date")
        return self


class CreateCycleArguments(StrictModel):
    project_id: UUID
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=10_000)
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> "CreateCycleArguments":
        if (self.start_date is None) != (self.end_date is None):
            raise ValueError("start_date and end_date must both be provided or omitted")
        if self.start_date is not None and self.end_date is not None and self.start_date > self.end_date:
            raise ValueError("start_date cannot exceed end_date")
        return self


class BulkUpdateIssuesArguments(IssueFields):
    project_id: UUID
    issue_ids: list[UUID] = Field(min_length=1, max_length=25)
    fields_to_update: list[
        Literal[
            "state_id",
            "priority",
            "start_date",
            "target_date",
            "assignee_ids",
            "label_ids",
            "cycle_id",
            "module_ids",
        ]
    ] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_changes(self) -> "BulkUpdateIssuesArguments":
        if len(set(self.fields_to_update)) != len(self.fields_to_update):
            raise ValueError("fields_to_update must not contain duplicates")
        return self


class ArchiveIssuesArguments(StrictModel):
    project_id: UUID
    issue_ids: list[UUID] = Field(min_length=1, max_length=25)
