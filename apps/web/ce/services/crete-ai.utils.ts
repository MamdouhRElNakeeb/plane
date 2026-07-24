/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type {
  ICreteAIMessage,
  ICreteAIProposal,
  ICreteAIRouteContext,
  ICreteAISSEEvent,
  ICreteAIThread,
  TCreteAIContextType,
  TCreteAIProposalType,
} from "@/plane-web/types/crete-ai";
import { CRETE_AI_CONTEXT_TYPES, CRETE_AI_PROPOSAL_TYPES } from "@/plane-web/types/crete-ai";

type TUnknownRecord = Record<string, unknown>;

const isRecord = (value: unknown): value is TUnknownRecord =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const readString = (record: TUnknownRecord, keys: string[]): string | undefined => {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.length > 0) return value;
    if (typeof value === "number") return value.toString();
  }
  return undefined;
};

export const createCreteAIId = (prefix: string): string => {
  const uuid =
    typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : Math.random().toString(36);
  return `${prefix}-${uuid}`;
};

export const isCreteAIContextType = (value: unknown): value is TCreteAIContextType =>
  typeof value === "string" && CRETE_AI_CONTEXT_TYPES.includes(value as TCreteAIContextType);

const isCreteAIProposalType = (value: unknown): value is TCreteAIProposalType =>
  typeof value === "string" && CRETE_AI_PROPOSAL_TYPES.includes(value as TCreteAIProposalType);

export const inferCreteAIContext = ({
  projectId,
  issueId,
}: ICreteAIRouteContext): {
  contextType: TCreteAIContextType;
  projectId?: string;
  issueId?: string;
} => {
  if (projectId && issueId) return { contextType: "work_item", projectId, issueId };
  if (projectId) return { contextType: "project", projectId };
  return { contextType: "workspace" };
};

export const normalizeCreteAIProposal = (value: unknown): ICreteAIProposal | undefined => {
  if (!isRecord(value)) return undefined;

  const proposalSource = isRecord(value.proposal) ? value.proposal : value;
  const type = readString(proposalSource, ["type", "action_type", "action_name", "action"]);
  if (!isCreteAIProposalType(type)) return undefined;

  const payloadValue = proposalSource.payload ?? proposalSource.arguments ?? proposalSource.data;
  const payload = isRecord(payloadValue) ? payloadValue : {};
  const rawStatus = readString(proposalSource, ["status"]);
  const status =
    rawStatus === "completed" || rawStatus === "confirmed" || rawStatus === "executed"
      ? "completed"
      : rawStatus === "error" || rawStatus === "failed"
        ? "error"
        : "pending";

  return {
    id: readString(proposalSource, ["id", "action_id", "proposal_id"]) ?? createCreteAIId("proposal"),
    messageId: readString(proposalSource, ["message_id", "messageId"]),
    type,
    payload,
    status,
    result: proposalSource.result,
    error: readString(proposalSource, ["error", "error_message"]),
  };
};

const normalizeProposalList = (value: unknown): ICreteAIProposal[] => {
  if (!Array.isArray(value)) return [];
  return value.map(normalizeCreteAIProposal).filter((proposal): proposal is ICreteAIProposal => !!proposal);
};

export const normalizeCreteAIMessage = (value: unknown): ICreteAIMessage | undefined => {
  if (!isRecord(value)) return undefined;

  const roleValue = readString(value, ["role", "message_type", "sender_type"]);
  const role = roleValue === "user" || roleValue === "system" ? roleValue : "assistant";
  const contentValue = value.content ?? value.text ?? value.message;
  const content =
    typeof contentValue === "string"
      ? contentValue
      : isRecord(contentValue)
        ? (readString(contentValue, ["content", "text"]) ?? "")
        : "";

  return {
    id: readString(value, ["id", "message_id"]) ?? createCreteAIId("message"),
    role,
    content,
    createdAt: readString(value, ["created_at", "createdAt"]),
    status: readString(value, ["status"]) === "error" ? "error" : "completed",
    proposals: normalizeProposalList(value.proposals ?? value.actions),
  };
};

const extractMessages = (value: TUnknownRecord): ICreteAIMessage[] | undefined => {
  const messagesValue = value.messages ?? value.history;
  if (!Array.isArray(messagesValue)) return undefined;
  return messagesValue.map(normalizeCreteAIMessage).filter((message): message is ICreteAIMessage => !!message);
};

export const normalizeCreteAIThread = (value: unknown): ICreteAIThread | undefined => {
  if (!isRecord(value)) return undefined;

  const threadSource = isRecord(value.thread) ? value.thread : value;
  const id = readString(threadSource, ["id", "thread_id"]);
  if (!id) return undefined;

  const rawContextType = threadSource.context_type ?? threadSource.contextType;

  const messages = extractMessages(threadSource) ?? extractMessages(value);
  const proposals = normalizeProposalList(
    threadSource.proposals ?? value.proposals ?? threadSource.pending_proposals ?? value.pending_proposals
  );
  if (proposals.length > 0 && messages) {
    for (const proposal of proposals) {
      const proposalMessage =
        (proposal.messageId ? messages.find((message) => message.id === proposal.messageId) : undefined) ??
        messages.reduceRight<ICreteAIMessage | undefined>(
          (found, message) => found ?? (message.role === "assistant" ? message : undefined),
          undefined
        );
      if (proposalMessage) proposalMessage.proposals.push(proposal);
      else
        messages.push({
          id: createCreteAIId("pending-actions"),
          role: "assistant",
          content: "This conversation has actions ready for review.",
          status: "completed",
          proposals: [proposal],
        });
    }
  }

  return {
    id,
    title: readString(threadSource, ["title", "name"]) ?? "New conversation",
    createdAt: readString(threadSource, ["created_at", "createdAt"]),
    updatedAt: readString(threadSource, ["updated_at", "updatedAt"]),
    contextType: isCreteAIContextType(rawContextType) ? rawContextType : undefined,
    projectId: readString(threadSource, ["project_id", "projectId"]),
    issueId: readString(threadSource, ["issue_id", "issueId", "work_item_id"]),
    messages,
  };
};

export const normalizeCreteAIThreadList = (value: unknown): ICreteAIThread[] => {
  let candidates: unknown = value;

  if (isRecord(value)) candidates = value.results ?? value.threads ?? value.data ?? [];
  if (isRecord(candidates)) candidates = candidates.results ?? candidates.threads ?? [];
  if (!Array.isArray(candidates)) return [];

  return candidates.map(normalizeCreteAIThread).filter((thread): thread is ICreteAIThread => !!thread);
};

export const getCreteAIErrorMessage = (
  error: unknown,
  fallback = "Something went wrong. Please try again."
): string => {
  if (typeof error === "string" && error.length > 0) return error;
  if (!isRecord(error)) return fallback;

  const nestedData = isRecord(error.data) ? error.data : isRecord(error.response) ? error.response : undefined;
  return (
    readString(error, ["error", "message", "detail"]) ??
    (nestedData ? readString(nestedData, ["error", "message", "detail"]) : undefined) ??
    fallback
  );
};

const parseSSEData = (rawData: string): unknown => {
  if (rawData === "[DONE]") return rawData;
  try {
    return JSON.parse(rawData);
  } catch {
    return rawData;
  }
};

export interface ICreteAISSEParser {
  feed: (chunk: string) => void;
  end: () => void;
}

export const createCreteAISSEParser = (onEvent: (event: ICreteAISSEEvent) => void): ICreteAISSEParser => {
  let buffer = "";
  let eventName = "message";
  let dataLines: string[] = [];

  const dispatch = () => {
    if (dataLines.length === 0) {
      eventName = "message";
      return;
    }

    const rawData = dataLines.join("\n");
    onEvent({
      event: rawData === "[DONE]" ? "done" : eventName,
      data: parseSSEData(rawData),
      rawData,
    });
    eventName = "message";
    dataLines = [];
  };

  const processLine = (line: string) => {
    if (line === "") {
      dispatch();
      return;
    }
    if (line.startsWith(":")) return;

    const separatorIndex = line.indexOf(":");
    const field = separatorIndex === -1 ? line : line.slice(0, separatorIndex);
    let fieldValue = separatorIndex === -1 ? "" : line.slice(separatorIndex + 1);
    if (fieldValue.startsWith(" ")) fieldValue = fieldValue.slice(1);

    if (field === "event") eventName = fieldValue || "message";
    if (field === "data") dataLines.push(fieldValue);
  };

  const drainCompleteLines = () => {
    let lineBreakIndex = buffer.indexOf("\n");
    while (lineBreakIndex !== -1) {
      const rawLine = buffer.slice(0, lineBreakIndex);
      buffer = buffer.slice(lineBreakIndex + 1);
      processLine(rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine);
      lineBreakIndex = buffer.indexOf("\n");
    }
  };

  return {
    feed: (chunk: string) => {
      buffer += chunk;
      drainCompleteLines();
    },
    end: () => {
      if (buffer.length > 0) processLine(buffer.endsWith("\r") ? buffer.slice(0, -1) : buffer);
      buffer = "";
      dispatch();
    },
  };
};
