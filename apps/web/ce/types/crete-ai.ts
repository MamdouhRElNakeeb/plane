/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

export const CRETE_AI_CONTEXT_TYPES = ["general", "workspace", "project", "work_item"] as const;

export type TCreteAIContextType = (typeof CRETE_AI_CONTEXT_TYPES)[number];

export const CRETE_AI_PROPOSAL_TYPES = ["create_comment", "edit_issue_description", "create_subtask"] as const;

export type TCreteAIProposalType = (typeof CRETE_AI_PROPOSAL_TYPES)[number];

export type TCreteAIProposalStatus = "pending" | "confirming" | "completed" | "error";

export type TCreteAIMessageStatus = "streaming" | "completed" | "error";

export interface ICreteAICitation {
  citationId: number;
  objectType: "issue";
  objectId: string;
  projectId: string;
  projectIdentifier: string;
  sequenceId: number;
  title: string;
}

export interface ICreteAIProposal {
  id: string;
  messageId?: string;
  type: TCreteAIProposalType;
  payload: Record<string, unknown>;
  status: TCreteAIProposalStatus;
  result?: unknown;
  error?: string;
}

export interface ICreteAIMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  createdAt?: string;
  status: TCreteAIMessageStatus;
  citations: ICreteAICitation[];
  proposals: ICreteAIProposal[];
}

export interface ICreteAIThread {
  id: string;
  title: string;
  createdAt?: string;
  updatedAt?: string;
  contextType?: TCreteAIContextType;
  projectId?: string;
  issueId?: string;
  messages?: ICreteAIMessage[];
}

export interface ICreteAIChatRequest {
  thread_id: string;
  prompt: string;
  context_type: TCreteAIContextType;
  project_id?: string;
  issue_id?: string;
}

export interface ICreteAICreateThreadRequest {
  title?: string;
  context_type: TCreteAIContextType;
  project_id?: string;
  issue_id?: string;
}

export interface ICreteAISSEEvent {
  event: string;
  data: unknown;
  rawData: string;
}

export interface ICreteAIRouteContext {
  projectId?: string;
  issueId?: string;
}

export const CRETE_AI_CONTEXT_LABELS: Record<TCreteAIContextType, string> = {
  general: "General",
  workspace: "Workspace",
  project: "Project",
  work_item: "Work item",
};
