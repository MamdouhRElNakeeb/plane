/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { MessageSquare, Plus, Trash2 } from "lucide-react";
import { Spinner } from "@plane/ui";
import { cn } from "@plane/utils";
import { useCreteAI } from "@/plane-web/hooks/use-crete-ai";

type TThreadSidebarProps = {
  workspaceSlug: string;
  onNewThread: () => void;
  onSelectThread: (threadId: string) => void;
  onDeleteThread: (threadId: string) => void;
};

export const CreteAIThreadSidebar = observer(function CreteAIThreadSidebar({
  workspaceSlug: _workspaceSlug,
  onNewThread,
  onSelectThread,
  onDeleteThread,
}: TThreadSidebarProps) {
  const { threads, activeThreadId, isLoadingThreads } = useCreteAI();

  return (
    <aside
      className="flex h-full w-64 shrink-0 flex-col border-r border-subtle-1 bg-layer-1"
      aria-label="AI conversations"
    >
      <div className="border-b border-subtle-1 p-3">
        <button
          type="button"
          className="focus-visible:ring-accent-primary flex h-8 w-full items-center justify-center gap-2 rounded-md border border-strong bg-layer-2 text-12 font-medium text-secondary hover:bg-layer-2-hover focus-visible:ring-1 focus-visible:outline-none"
          onClick={onNewThread}
        >
          <Plus className="size-4" aria-hidden="true" />
          New conversation
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {isLoadingThreads ? (
          <div className="flex justify-center py-6" aria-label="Loading conversations">
            <Spinner className="size-4" />
          </div>
        ) : threads.length === 0 ? (
          <p className="px-2 py-6 text-center text-11 text-tertiary">Your conversations will appear here.</p>
        ) : (
          <ul className="space-y-1">
            {threads.map((thread) => (
              <li key={thread.id}>
                <div
                  className={cn("group flex items-center rounded-md", {
                    "bg-layer-transparent-selected": activeThreadId === thread.id,
                    "hover:bg-layer-transparent-hover": activeThreadId !== thread.id,
                  })}
                >
                  <button
                    type="button"
                    className="focus-visible:ring-accent-primary flex min-w-0 flex-1 items-center gap-2 px-2 py-2 text-left focus-visible:ring-1 focus-visible:outline-none"
                    onClick={() => onSelectThread(thread.id)}
                    aria-current={activeThreadId === thread.id ? "page" : undefined}
                  >
                    <MessageSquare className="size-3.5 shrink-0 text-tertiary" aria-hidden="true" />
                    <span className="truncate text-12 text-secondary">{thread.title}</span>
                  </button>
                  <button
                    type="button"
                    className="focus-visible:ring-accent-primary mr-1 grid size-7 shrink-0 place-items-center rounded text-tertiary opacity-0 group-hover:opacity-100 hover:bg-layer-transparent-hover hover:text-danger-primary focus:opacity-100 focus-visible:ring-1 focus-visible:outline-none"
                    onClick={() => onDeleteThread(thread.id)}
                    aria-label={`Delete ${thread.title}`}
                  >
                    <Trash2 className="size-3.5" aria-hidden="true" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
});
