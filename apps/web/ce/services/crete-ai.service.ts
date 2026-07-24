/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { API_BASE_URL } from "@plane/constants";
import { APIService } from "@/services/api.service";
import type { ICreteAIChatRequest, ICreteAICreateThreadRequest, ICreteAISSEEvent } from "@/plane-web/types/crete-ai";
import { createCreteAISSEParser, getCreteAIErrorMessage } from "./crete-ai.utils";

const workspaceBasePath = (workspaceSlug: string) => `/api/crete-ai/workspaces/${encodeURIComponent(workspaceSlug)}`;

export class CreteAIService extends APIService {
  private csrfTokenPromise: Promise<string> | undefined;

  constructor() {
    super(API_BASE_URL);
  }

  private async getCSRFToken(): Promise<string> {
    if (!this.csrfTokenPromise) {
      this.csrfTokenPromise = this.get("/auth/get-csrf-token/")
        .then((response) => {
          const token = response.data?.csrf_token;
          if (typeof token !== "string" || !token) throw new Error("CSRF token is unavailable.");
          return token;
        })
        .catch((error) => {
          this.csrfTokenPromise = undefined;
          throw error;
        });
    }
    return this.csrfTokenPromise;
  }

  async listThreads(workspaceSlug: string): Promise<unknown> {
    return this.get(`${workspaceBasePath(workspaceSlug)}/threads/`)
      .then((response) => response.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }

  async createThread(workspaceSlug: string, data: ICreteAICreateThreadRequest): Promise<unknown> {
    const csrfToken = await this.getCSRFToken();
    return this.post(`${workspaceBasePath(workspaceSlug)}/threads/`, data, {
      headers: { "X-CSRFToken": csrfToken },
    })
      .then((response) => response.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }

  async getThread(workspaceSlug: string, threadId: string): Promise<unknown> {
    return this.get(`${workspaceBasePath(workspaceSlug)}/threads/${encodeURIComponent(threadId)}/`)
      .then((response) => response.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }

  async deleteThread(workspaceSlug: string, threadId: string): Promise<void> {
    const csrfToken = await this.getCSRFToken();
    return this.delete(`${workspaceBasePath(workspaceSlug)}/threads/${encodeURIComponent(threadId)}/`, undefined, {
      headers: { "X-CSRFToken": csrfToken },
    })
      .then(() => undefined)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }

  async confirmAction(workspaceSlug: string, actionId: string): Promise<unknown> {
    const csrfToken = await this.getCSRFToken();
    return this.post(
      `${workspaceBasePath(workspaceSlug)}/actions/${encodeURIComponent(actionId)}/confirm/`,
      undefined,
      {
        headers: { "X-CSRFToken": csrfToken },
      }
    )
      .then((response) => response.data)
      .catch((error) => {
        throw error?.response?.data ?? error;
      });
  }

  async streamChat(
    workspaceSlug: string,
    data: ICreteAIChatRequest,
    signal: AbortSignal,
    onEvent: (event: ICreteAISSEEvent) => void
  ): Promise<void> {
    const csrfToken = await this.getCSRFToken();
    const requestInit: RequestInit = {
      method: "POST",
      credentials: "include",
      headers: {
        Accept: "text/event-stream",
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken,
      },
      body: JSON.stringify(data),
      signal,
    };
    const baseURL = `${API_BASE_URL.replace(/\/$/, "")}${workspaceBasePath(workspaceSlug)}`;
    const response = await fetch(`${baseURL}/threads/${encodeURIComponent(data.thread_id)}/chat/`, requestInit);

    if (!response.ok) {
      let errorBody: unknown;
      try {
        errorBody = await response.json();
      } catch {
        errorBody = await response.text().catch(() => undefined);
      }
      throw new Error(getCreteAIErrorMessage(errorBody, `Assistant request failed (${response.status}).`));
    }

    if (!response.body) throw new Error("The assistant returned an empty stream.");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    const parser = createCreteAISSEParser(onEvent);

    try {
      const readNextChunk = async (): Promise<void> => {
        const { done, value } = await reader.read();
        if (done) return;
        parser.feed(decoder.decode(value, { stream: true }));
        return readNextChunk();
      };
      await readNextChunk();
      parser.feed(decoder.decode());
      parser.end();
    } finally {
      reader.releaseLock();
    }
  }
}
