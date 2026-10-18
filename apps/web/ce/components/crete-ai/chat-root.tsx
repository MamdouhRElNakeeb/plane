/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useRef } from "react";
import { observer } from "mobx-react";
import { useRouter } from "next/navigation";
import { ExternalLink, Plus, X } from "lucide-react";
import { AiIcon } from "@plane/propel/icons";
import { useCreteAI } from "@/plane-web/hooks/use-crete-ai";
import type { ICreteAIRouteContext } from "@/plane-web/types/crete-ai";
import { CreteAIChatMessages } from "./chat-messages";
import { CreteAIComposer } from "./composer";
import { CreteAIContextSelector } from "./context-selector";
import { CreteAIThreadSidebar } from "./thread-sidebar";

type TChatRootProps = {
  workspaceSlug: string;
  threadId?: string;
  routeContext?: ICreteAIRouteContext;
  variant: "panel" | "page";
  onClose?: () => void;
};

export const CreteAIChatRoot = observer(function CreteAIChatRoot({
  workspaceSlug,
  threadId,
  routeContext,
  variant,
  onClose,
}: TChatRootProps) {
  const router = useRouter();
  const { activeThreadId, loadThreads, selectThread, deleteThread, sendPrompt, startNewThread, setPanelOpen } =
    useCreteAI();
  const shouldNavigateToCreatedThread = useRef(false);
  const routeProjectId = routeContext?.projectId;
  const routeIssueId = routeContext?.issueId;

  useEffect(() => {
    void loadThreads(workspaceSlug, {
      projectId: routeProjectId,
      issueId: routeIssueId,
    });
  }, [loadThreads, routeIssueId, routeProjectId, workspaceSlug]);

  useEffect(() => {
    if (variant !== "page") return;
    if (threadId) void selectThread(workspaceSlug, threadId);
    else startNewThread();
  }, [selectThread, startNewThread, threadId, variant, workspaceSlug]);

  useEffect(() => {
    if (variant === "page" && shouldNavigateToCreatedThread.current && activeThreadId) {
      shouldNavigateToCreatedThread.current = false;
      router.replace(`/${workspaceSlug}/pi-chat/${activeThreadId}`);
    }
  }, [activeThreadId, router, variant, workspaceSlug]);

  const handlePrompt = (prompt: string) => {
    if (variant === "page" && !activeThreadId) shouldNavigateToCreatedThread.current = true;
    void sendPrompt(workspaceSlug, prompt);
  };

  const handleNewThread = () => {
    shouldNavigateToCreatedThread.current = false;
    startNewThread(routeContext);
    if (variant === "page") router.push(`/${workspaceSlug}/pi-chat`);
  };

  const handleDeleteThread = (id: string) => {
    const deletingActiveThread = id === activeThreadId;
    void deleteThread(workspaceSlug, id).then(() => {
      if (variant === "page" && deletingActiveThread) router.push(`/${workspaceSlug}/pi-chat`);
      return undefined;
    });
  };

  return (
    <div className="flex size-full min-h-0 overflow-hidden bg-surface-1">
      {variant === "page" && (
        <CreteAIThreadSidebar
          workspaceSlug={workspaceSlug}
          onNewThread={handleNewThread}
          onSelectThread={(id) => router.push(`/${workspaceSlug}/pi-chat/${id}`)}
          onDeleteThread={handleDeleteThread}
        />
      )}

      <section className="flex min-w-0 flex-1 flex-col" aria-label="AI assistant">
        <header className="flex h-12 shrink-0 items-center justify-between border-b border-subtle-1 bg-layer-1 px-3">
          <div className="flex min-w-0 items-center gap-2">
            <span className="grid size-7 shrink-0 place-items-center rounded-md bg-accent-subtle text-accent-primary">
              <AiIcon className="size-4" />
            </span>
            <div className="min-w-0">
              <h1 className="truncate text-13 font-semibold text-primary">Crete AI</h1>
              <p className="truncate text-10 text-tertiary">Your workspace assistant</p>
            </div>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              className="focus-visible:ring-accent-primary grid size-8 place-items-center rounded-md text-tertiary hover:bg-layer-transparent-hover hover:text-secondary focus-visible:ring-1 focus-visible:outline-none"
              onClick={handleNewThread}
              aria-label="Start a new AI conversation"
              title="New conversation"
            >
              <Plus className="size-4" aria-hidden="true" />
            </button>
            {variant === "panel" && (
              <>
                <button
                  type="button"
                  className="focus-visible:ring-accent-primary grid size-8 place-items-center rounded-md text-tertiary hover:bg-layer-transparent-hover hover:text-secondary focus-visible:ring-1 focus-visible:outline-none"
                  onClick={() => {
                    setPanelOpen(false);
                    router.push(
                      activeThreadId ? `/${workspaceSlug}/pi-chat/${activeThreadId}` : `/${workspaceSlug}/pi-chat`
                    );
                  }}
                  aria-label="Open AI assistant page"
                  title="Open full page"
                >
                  <ExternalLink className="size-4" aria-hidden="true" />
                </button>
                <button
                  type="button"
                  className="focus-visible:ring-accent-primary grid size-8 place-items-center rounded-md text-tertiary hover:bg-layer-transparent-hover hover:text-secondary focus-visible:ring-1 focus-visible:outline-none"
                  onClick={onClose}
                  aria-label="Close AI assistant"
                >
                  <X className="size-4" aria-hidden="true" />
                </button>
              </>
            )}
          </div>
        </header>

        <CreteAIContextSelector workspaceSlug={workspaceSlug} />
        <CreteAIChatMessages workspaceSlug={workspaceSlug} onPrompt={handlePrompt} />
        <CreteAIComposer onPrompt={handlePrompt} />
      </section>
    </div>
  );
});
