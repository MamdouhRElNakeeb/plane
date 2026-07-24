/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { action, computed, makeObservable, observable, runInAction } from "mobx";
import type { RootStore } from "@/plane-web/store/root.store";
import { CreteAIService } from "@/plane-web/services/crete-ai.service";
import {
  createCreteAIId,
  getCreteAIErrorMessage,
  inferCreteAIContext,
  isCreteAIContextType,
  normalizeCreteAIMessage,
  normalizeCreteAIProposal,
  normalizeCreteAIThread,
  normalizeCreteAIThreadList,
} from "@/plane-web/services/crete-ai.utils";
import type {
  ICreteAIMessage,
  ICreteAIProposal,
  ICreteAIRouteContext,
  ICreteAISSEEvent,
  ICreteAIThread,
  TCreteAIContextType,
} from "@/plane-web/types/crete-ai";

type TUnknownRecord = Record<string, unknown>;

const isRecord = (value: unknown): value is TUnknownRecord =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const getString = (value: unknown, keys: string[]): string | undefined => {
  if (!isRecord(value)) return typeof value === "string" ? value : undefined;
  for (const key of keys) {
    const candidate = value[key];
    if (typeof candidate === "string") return candidate;
  }
  return undefined;
};

const unwrapData = (value: unknown): unknown => (isRecord(value) && value.data !== undefined ? value.data : value);

export interface ICreteAIStore {
  threads: ICreteAIThread[];
  activeThreadId: string | undefined;
  messages: ICreteAIMessage[];
  isPanelOpen: boolean;
  isLoadingThreads: boolean;
  isLoadingThread: boolean;
  isStreaming: boolean;
  error: string | undefined;
  lastPrompt: string | undefined;
  contextType: TCreteAIContextType;
  selectedProjectId: string | undefined;
  selectedIssueId: string | undefined;
  activeThread: ICreteAIThread | undefined;
  setPanelOpen: (isOpen: boolean, routeContext?: ICreteAIRouteContext) => void;
  togglePanel: (routeContext?: ICreteAIRouteContext) => void;
  setContextType: (contextType: TCreteAIContextType) => void;
  setProjectId: (projectId: string | undefined) => void;
  setIssueId: (issueId: string | undefined) => void;
  startNewThread: (routeContext?: ICreteAIRouteContext) => void;
  loadThreads: (workspaceSlug: string, routeContext?: ICreteAIRouteContext) => Promise<void>;
  selectThread: (workspaceSlug: string, threadId: string) => Promise<ICreteAIThread | undefined>;
  deleteThread: (workspaceSlug: string, threadId: string) => Promise<void>;
  sendPrompt: (workspaceSlug: string, prompt: string) => Promise<string | undefined>;
  confirmProposal: (workspaceSlug: string, proposalId: string) => Promise<void>;
  reset: () => void;
}

export class CreteAIStore implements ICreteAIStore {
  threads: ICreteAIThread[] = [];
  activeThreadId: string | undefined = undefined;
  messages: ICreteAIMessage[] = [];
  isPanelOpen = false;
  isLoadingThreads = false;
  isLoadingThread = false;
  isStreaming = false;
  error: string | undefined = undefined;
  lastPrompt: string | undefined = undefined;
  contextType: TCreteAIContextType = "workspace";
  selectedProjectId: string | undefined = undefined;
  selectedIssueId: string | undefined = undefined;

  private service: CreteAIService;
  private streamController: AbortController | undefined;
  private streamingMessageId: string | undefined;
  private workspaceSlug: string | undefined;

  constructor(_rootStore: RootStore) {
    makeObservable<this, "handleStreamEvent">(this, {
      threads: observable,
      activeThreadId: observable,
      messages: observable,
      isPanelOpen: observable,
      isLoadingThreads: observable,
      isLoadingThread: observable,
      isStreaming: observable,
      error: observable,
      lastPrompt: observable,
      contextType: observable,
      selectedProjectId: observable,
      selectedIssueId: observable,
      activeThread: computed,
      setPanelOpen: action,
      togglePanel: action,
      setContextType: action,
      setProjectId: action,
      setIssueId: action,
      startNewThread: action,
      handleStreamEvent: action,
      reset: action,
    });

    this.service = new CreteAIService();
  }

  get activeThread(): ICreteAIThread | undefined {
    return this.threads.find((thread) => thread.id === this.activeThreadId);
  }

  setPanelOpen = (isOpen: boolean, routeContext?: ICreteAIRouteContext) => {
    this.isPanelOpen = isOpen;
    if (isOpen && !this.activeThreadId && routeContext) this.applyRouteContext(routeContext);
  };

  togglePanel = (routeContext?: ICreteAIRouteContext) => {
    this.setPanelOpen(!this.isPanelOpen, routeContext);
  };

  setContextType = (contextType: TCreteAIContextType) => {
    this.contextType = contextType;
    if (contextType === "general" || contextType === "workspace") {
      this.selectedProjectId = undefined;
      this.selectedIssueId = undefined;
    } else if (contextType === "project") {
      this.selectedIssueId = undefined;
    }
  };

  setProjectId = (projectId: string | undefined) => {
    this.selectedProjectId = projectId;
    if (!projectId) this.selectedIssueId = undefined;
  };

  setIssueId = (issueId: string | undefined) => {
    this.selectedIssueId = issueId;
  };

  startNewThread = (routeContext?: ICreteAIRouteContext) => {
    this.cancelStream();
    this.activeThreadId = undefined;
    this.messages = [];
    this.error = undefined;
    this.lastPrompt = undefined;
    if (routeContext) this.applyRouteContext(routeContext);
  };

  loadThreads = async (workspaceSlug: string, routeContext?: ICreteAIRouteContext) => {
    if (this.workspaceSlug && this.workspaceSlug !== workspaceSlug) {
      this.startNewThread(routeContext);
      this.threads = [];
    } else if (!this.activeThreadId && routeContext) {
      this.applyRouteContext(routeContext);
    }
    this.workspaceSlug = workspaceSlug;
    this.isLoadingThreads = true;
    try {
      const response = await this.service.listThreads(workspaceSlug);
      const threads = normalizeCreteAIThreadList(response);
      runInAction(() => {
        if (this.workspaceSlug !== workspaceSlug) return;
        this.threads = threads;
      });
    } catch (error) {
      runInAction(() => {
        if (this.workspaceSlug !== workspaceSlug) return;
        this.error = getCreteAIErrorMessage(error, "Could not load assistant conversations.");
      });
    } finally {
      runInAction(() => {
        if (this.workspaceSlug !== workspaceSlug) return;
        this.isLoadingThreads = false;
      });
    }
  };

  selectThread = async (workspaceSlug: string, threadId: string): Promise<ICreteAIThread | undefined> => {
    if (this.activeThreadId === threadId && this.messages.length > 0) return this.activeThread;

    this.cancelStream();
    this.isLoadingThread = true;
    this.error = undefined;
    try {
      const response = await this.service.getThread(workspaceSlug, threadId);
      const thread = normalizeCreteAIThread(unwrapData(response));
      if (!thread) throw new Error("The assistant returned an invalid conversation.");

      runInAction(() => {
        this.upsertThread(thread);
        this.activeThreadId = thread.id;
        this.messages = thread.messages ?? [];
        this.applyThreadContext(thread);
      });
      return thread;
    } catch (error) {
      runInAction(() => {
        this.error = getCreteAIErrorMessage(error, "Could not load this conversation.");
      });
      return undefined;
    } finally {
      runInAction(() => {
        this.isLoadingThread = false;
      });
    }
  };

  deleteThread = async (workspaceSlug: string, threadId: string) => {
    try {
      await this.service.deleteThread(workspaceSlug, threadId);
      runInAction(() => {
        this.threads = this.threads.filter((thread) => thread.id !== threadId);
        if (this.activeThreadId === threadId) this.startNewThread();
      });
    } catch (error) {
      runInAction(() => {
        this.error = getCreteAIErrorMessage(error, "Could not delete this conversation.");
      });
      throw error;
    }
  };

  sendPrompt = async (workspaceSlug: string, prompt: string): Promise<string | undefined> => {
    const trimmedPrompt = prompt.trim();
    if (!trimmedPrompt || this.isLoadingThread) return this.activeThreadId;
    if ((this.contextType === "project" || this.contextType === "work_item") && !this.selectedProjectId) {
      this.error = "Choose a project before sending this prompt.";
      return this.activeThreadId;
    }
    if (this.contextType === "work_item" && !this.selectedIssueId) {
      this.error = "Open a work item or load a work-item conversation before using this context.";
      return this.activeThreadId;
    }

    this.cancelStream();
    this.error = undefined;
    this.lastPrompt = trimmedPrompt;

    let threadId = this.activeThreadId;
    if (!threadId) {
      try {
        const response = await this.service.createThread(workspaceSlug, {
          context_type: this.contextType,
          ...(this.selectedProjectId ? { project_id: this.selectedProjectId } : {}),
          ...(this.selectedIssueId ? { issue_id: this.selectedIssueId } : {}),
        });
        const thread = normalizeCreteAIThread(unwrapData(response));
        if (!thread) throw new Error("The assistant did not return a conversation ID.");
        threadId = thread.id;
        runInAction(() => {
          this.upsertThread(thread);
          this.activeThreadId = thread.id;
          if (thread.messages?.length) this.messages = thread.messages;
        });
      } catch (error) {
        runInAction(() => {
          this.error = getCreteAIErrorMessage(error, "Could not start a new conversation.");
        });
        return undefined;
      }
    }

    const userMessage: ICreteAIMessage = {
      id: createCreteAIId("user"),
      role: "user",
      content: trimmedPrompt,
      status: "completed",
      proposals: [],
    };
    const assistantMessage: ICreteAIMessage = {
      id: createCreteAIId("assistant"),
      role: "assistant",
      content: "",
      status: "streaming",
      proposals: [],
    };
    const controller = new AbortController();

    runInAction(() => {
      this.messages.push(userMessage, assistantMessage);
      this.streamingMessageId = assistantMessage.id;
      this.streamController = controller;
      this.isStreaming = true;
    });

    try {
      await this.service.streamChat(
        workspaceSlug,
        {
          thread_id: threadId,
          prompt: trimmedPrompt,
          context_type: this.contextType,
          ...(this.selectedProjectId ? { project_id: this.selectedProjectId } : {}),
          ...(this.selectedIssueId ? { issue_id: this.selectedIssueId } : {}),
        },
        controller.signal,
        this.handleStreamEvent
      );

      runInAction(() => {
        const message = this.getStreamingMessage();
        if (message && message.status === "streaming") message.status = "completed";
      });
    } catch (error) {
      if (!controller.signal.aborted) {
        runInAction(() => {
          const message = this.getStreamingMessage();
          if (message) message.status = "error";
          this.error = getCreteAIErrorMessage(error, "The assistant response was interrupted.");
        });
      }
    } finally {
      let shouldRefreshThreads = false;
      runInAction(() => {
        if (this.streamController === controller) {
          this.isStreaming = false;
          this.streamController = undefined;
          this.streamingMessageId = undefined;
          shouldRefreshThreads = true;
        }
      });
      if (shouldRefreshThreads) void this.loadThreads(workspaceSlug);
    }

    return threadId;
  };

  confirmProposal = async (workspaceSlug: string, proposalId: string) => {
    const proposal = this.findProposal(proposalId);
    if (!proposal || proposal.status === "confirming" || proposal.status === "completed") return;

    runInAction(() => {
      proposal.status = "confirming";
      proposal.error = undefined;
    });

    try {
      const result = await this.service.confirmAction(workspaceSlug, proposalId);
      runInAction(() => {
        proposal.status = "completed";
        proposal.result = result;
      });
    } catch (error) {
      runInAction(() => {
        proposal.status = "error";
        proposal.error = getCreteAIErrorMessage(error, "This action could not be completed.");
      });
    }
  };

  reset = () => {
    this.cancelStream();
    this.threads = [];
    this.activeThreadId = undefined;
    this.messages = [];
    this.isPanelOpen = false;
    this.isLoadingThreads = false;
    this.isLoadingThread = false;
    this.isStreaming = false;
    this.error = undefined;
    this.lastPrompt = undefined;
    this.contextType = "workspace";
    this.selectedProjectId = undefined;
    this.selectedIssueId = undefined;
    this.workspaceSlug = undefined;
  };

  private handleStreamEvent = (event: ICreteAISSEEvent) => {
    const eventData = unwrapData(event.data);
    const embeddedType = getString(eventData, ["event", "type"]);
    const eventType = event.event === "message" && embeddedType ? embeddedType : event.event;
    const message = this.getStreamingMessage();

    if (eventType === "message.delta") {
      const delta = getString(eventData, ["delta", "text", "content"]) ?? getString(event.data, ["delta", "text"]);
      if (message && delta) message.content += delta;
      return;
    }

    if (eventType === "proposal.created") {
      const proposalValue = isRecord(eventData) ? (eventData.proposal ?? eventData.action ?? eventData) : eventData;
      const proposal = normalizeCreteAIProposal(proposalValue);
      if (message && proposal && !message.proposals.some((item) => item.id === proposal.id))
        message.proposals.push(proposal);
      return;
    }

    if (eventType === "message.completed") {
      if (!message) return;
      const completedMessageValue = isRecord(eventData) ? (eventData.message ?? eventData) : eventData;
      const normalizedMessage = normalizeCreteAIMessage(completedMessageValue);
      const completedText =
        normalizedMessage?.content || getString(completedMessageValue, ["content", "text", "delta"]) || "";
      if (completedText && !message.content) message.content = completedText;
      if (normalizedMessage?.proposals.length) {
        for (const proposal of normalizedMessage.proposals) {
          if (!message.proposals.some((item) => item.id === proposal.id)) message.proposals.push(proposal);
        }
      }
      message.status = "completed";
      return;
    }

    if (eventType === "error") {
      if (message) message.status = "error";
      this.error = getCreteAIErrorMessage(eventData, "The assistant could not complete this response.");
      return;
    }

    if (eventType === "done" && message?.status === "streaming") message.status = "completed";
  };

  private applyRouteContext(routeContext: ICreteAIRouteContext) {
    const context = inferCreteAIContext(routeContext);
    this.contextType = context.contextType;
    this.selectedProjectId = context.projectId;
    this.selectedIssueId = context.issueId;
  }

  private applyThreadContext(thread: ICreteAIThread) {
    if (thread.contextType && isCreteAIContextType(thread.contextType)) this.contextType = thread.contextType;
    else this.contextType = inferCreteAIContext({ projectId: thread.projectId, issueId: thread.issueId }).contextType;
    this.selectedProjectId = thread.projectId;
    this.selectedIssueId = thread.issueId;
  }

  private upsertThread(thread: ICreteAIThread) {
    const existingIndex = this.threads.findIndex((item) => item.id === thread.id);
    if (existingIndex === -1) this.threads.unshift(thread);
    else this.threads[existingIndex] = { ...this.threads[existingIndex], ...thread };
  }

  private getStreamingMessage(): ICreteAIMessage | undefined {
    return this.messages.find((message) => message.id === this.streamingMessageId);
  }

  private findProposal(proposalId: string): ICreteAIProposal | undefined {
    for (const message of this.messages) {
      const proposal = message.proposals.find((item) => item.id === proposalId);
      if (proposal) return proposal;
    }
    return undefined;
  }

  private cancelStream() {
    this.streamController?.abort();
    this.streamController = undefined;
    this.streamingMessageId = undefined;
    this.isStreaming = false;
  }
}
