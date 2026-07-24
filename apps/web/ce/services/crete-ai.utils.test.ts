/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it } from "vitest";
import {
  createCreteAISSEParser,
  inferCreteAIContext,
  normalizeCreteAIProposal,
  normalizeCreteAIThreadList,
} from "./crete-ai.utils";

describe("createCreteAISSEParser", () => {
  it("parses named events split across arbitrary chunks", () => {
    const events: { event: string; data: unknown }[] = [];
    const parser = createCreteAISSEParser(({ event, data }) => events.push({ event, data }));

    parser.feed("event: message.del");
    parser.feed('ta\r\ndata: {"del');
    parser.feed('ta":"Hello"}\r\n\r\nevent: proposal.created\n');
    parser.feed('data: {"proposal":{"id":"action-1","type":"create_comment","payload":{"comment":"Hi"}}}\n\n');
    parser.end();

    expect(events).toEqual([
      { event: "message.delta", data: { delta: "Hello" } },
      {
        event: "proposal.created",
        data: {
          proposal: {
            id: "action-1",
            type: "create_comment",
            payload: { comment: "Hi" },
          },
        },
      },
    ]);
  });

  it("handles generic events, comments, multiple data lines, and DONE", () => {
    const events: { event: string; data: unknown; rawData: string }[] = [];
    const parser = createCreteAISSEParser((event) => events.push(event));

    parser.feed(": keep-alive\n\ndata: first\ndata: second\n\ndata: [DONE]\n");
    parser.end();

    expect(events).toEqual([
      { event: "message", data: "first\nsecond", rawData: "first\nsecond" },
      { event: "done", data: "[DONE]", rawData: "[DONE]" },
    ]);
  });

  it("flushes the final event without a trailing newline", () => {
    const events: unknown[] = [];
    const parser = createCreteAISSEParser((event) => events.push(event));

    parser.feed('event: message.completed\ndata: {"text":"Done"}');
    parser.end();

    expect(events).toEqual([
      {
        event: "message.completed",
        data: { text: "Done" },
        rawData: '{"text":"Done"}',
      },
    ]);
  });
});

describe("Crete AI normalization utilities", () => {
  it("infers the narrowest available route context", () => {
    expect(inferCreteAIContext({})).toEqual({ contextType: "workspace" });
    expect(inferCreteAIContext({ projectId: "project-1" })).toEqual({
      contextType: "project",
      projectId: "project-1",
    });
    expect(inferCreteAIContext({ projectId: "project-1", issueId: "issue-1" })).toEqual({
      contextType: "work_item",
      projectId: "project-1",
      issueId: "issue-1",
    });
  });

  it("normalizes defensive thread-list response shapes", () => {
    expect(
      normalizeCreteAIThreadList({
        data: {
          results: [
            {
              thread_id: "thread-1",
              name: "Planning",
              context_type: "project",
              project_id: "project-1",
            },
          ],
        },
      })
    ).toEqual([
      {
        id: "thread-1",
        title: "Planning",
        contextType: "project",
        projectId: "project-1",
        createdAt: undefined,
        updatedAt: undefined,
        issueId: undefined,
        messages: undefined,
      },
    ]);
  });

  it("accepts supported proposal aliases and rejects unsupported actions", () => {
    expect(
      normalizeCreteAIProposal({
        action_id: "action-1",
        action_name: "create_subtask",
        arguments: { name: "Follow up" },
      })
    ).toMatchObject({
      id: "action-1",
      type: "create_subtask",
      payload: { name: "Follow up" },
      status: "pending",
    });
    expect(normalizeCreteAIProposal({ id: "action-2", type: "delete_issue" })).toBeUndefined();
  });

  it("attaches persisted proposals to their assistant messages", () => {
    const [thread] = normalizeCreteAIThreadList({
      threads: [
        {
          id: "thread-1",
          messages: [
            { id: "message-1", role: "user", content: "Help" },
            { id: "message-2", role: "assistant", content: "I can create that." },
            { id: "message-3", role: "assistant", content: "Later response." },
          ],
          proposals: [
            {
              id: "action-1",
              message_id: "message-2",
              action_name: "create_comment",
              arguments: { comment_html: "<p>Update</p>" },
              status: "completed",
            },
          ],
        },
      ],
    });

    expect(thread.messages?.[1].proposals).toEqual([
      expect.objectContaining({ id: "action-1", type: "create_comment", status: "completed" }),
    ]);
    expect(thread.messages?.[2].proposals).toEqual([]);
  });
});
