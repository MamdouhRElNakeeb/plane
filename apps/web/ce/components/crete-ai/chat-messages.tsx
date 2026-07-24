/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useRef } from "react";
import { observer } from "mobx-react";
import ReactMarkdown from "react-markdown";
import { Bot, CircleAlert } from "lucide-react";
import { Button } from "@plane/propel/button";
import { AiIcon } from "@plane/propel/icons";
import { Spinner } from "@plane/ui";
import { cn } from "@plane/utils";
import { useCreteAI } from "@/plane-web/hooks/use-crete-ai";
import { CreteAIProposalCard } from "./proposal-card";

type TChatMessagesProps = {
  workspaceSlug: string;
  onPrompt: (prompt: string) => void;
};

const SUGGESTIONS = ["Summarize the current context", "Identify blockers and risks", "Draft practical next steps"];

const Markdown = ({ content }: { content: string }) => (
  <div className="space-y-2 text-13 leading-5 break-words text-secondary">
    <ReactMarkdown
      skipHtml
      components={{
        a: ({ children, href }) => (
          <a
            href={href}
            className="text-link-primary underline hover:text-link-primary-hover"
            target="_blank"
            rel="noopener noreferrer"
          >
            {children}
          </a>
        ),
        blockquote: ({ children }) => (
          <blockquote className="border-l-2 border-strong pl-3 text-tertiary">{children}</blockquote>
        ),
        code: ({ children }) => (
          <code className="font-mono rounded bg-layer-3 px-1 py-0.5 text-12 text-primary">{children}</code>
        ),
        h1: ({ children }) => <h1 className="text-16 font-semibold text-primary">{children}</h1>,
        h2: ({ children }) => <h2 className="text-14 font-semibold text-primary">{children}</h2>,
        h3: ({ children }) => <h3 className="text-13 font-semibold text-primary">{children}</h3>,
        ol: ({ children }) => <ol className="ml-5 list-decimal space-y-1">{children}</ol>,
        p: ({ children }) => <p className="whitespace-pre-wrap">{children}</p>,
        pre: ({ children }) => <pre className="overflow-x-auto rounded-md bg-layer-3 p-2">{children}</pre>,
        ul: ({ children }) => <ul className="ml-5 list-disc space-y-1">{children}</ul>,
      }}
    >
      {content}
    </ReactMarkdown>
  </div>
);

export const CreteAIChatMessages = observer(function CreteAIChatMessages({
  workspaceSlug,
  onPrompt,
}: TChatMessagesProps) {
  const { messages, isLoadingThread, isStreaming, error, lastPrompt, confirmProposal } = useCreteAI();
  const bottomRef = useRef<HTMLDivElement>(null);
  const messageSignature = messages.map((message) => `${message.id}:${message.content.length}`).join("|");

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: isStreaming ? "smooth" : "auto", block: "end" });
  }, [isStreaming, messageSignature]);

  if (isLoadingThread)
    return (
      <div className="flex flex-1 items-center justify-center" aria-label="Loading conversation">
        <Spinner className="size-5" />
      </div>
    );

  if (messages.length === 0)
    return (
      <div className="flex flex-1 flex-col items-center justify-center px-5 py-8 text-center">
        <div className="grid size-10 place-items-center rounded-lg bg-accent-subtle text-accent-primary">
          <AiIcon className="size-5" />
        </div>
        <h2 className="mt-3 text-16 font-semibold text-primary">How can I help?</h2>
        <p className="mt-1 max-w-sm text-12 leading-5 text-tertiary">
          Ask about your workspace, a project, or the work item you are viewing.
        </p>
        <div className="mt-5 flex w-full max-w-md flex-col gap-2">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              className="focus-visible:ring-accent-primary rounded-md border border-subtle-1 bg-layer-2 px-3 py-2 text-left text-12 text-secondary transition-colors hover:bg-layer-2-hover focus-visible:ring-1 focus-visible:outline-none"
              onClick={() => onPrompt(suggestion)}
            >
              {suggestion}
            </button>
          ))}
        </div>
      </div>
    );

  return (
    <div className="flex-1 overflow-y-auto px-4 py-4" aria-live="polite">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
        {messages.map((message) => (
          <article
            key={message.id}
            className={cn("flex gap-2.5", {
              "justify-end": message.role === "user",
              "justify-start": message.role !== "user",
            })}
          >
            {message.role !== "user" && (
              <div className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-md bg-accent-subtle text-accent-primary">
                <Bot className="size-4" aria-hidden="true" />
              </div>
            )}
            <div
              className={cn("max-w-[88%] min-w-0 rounded-xl px-3 py-2", {
                "bg-accent-primary text-on-color": message.role === "user",
                "border border-subtle-1 bg-layer-1": message.role !== "user",
              })}
            >
              {message.role === "user" ? (
                <p className="text-13 leading-5 whitespace-pre-wrap">{message.content}</p>
              ) : message.content ? (
                <Markdown content={message.content} />
              ) : message.status === "streaming" ? (
                <span className="flex items-center gap-2 py-1 text-12 text-tertiary">
                  <Spinner className="size-3.5" /> Thinking
                </span>
              ) : null}

              {message.proposals.map((proposal) => (
                <CreteAIProposalCard
                  key={proposal.id}
                  proposal={proposal}
                  onConfirm={(proposalId) => void confirmProposal(workspaceSlug, proposalId)}
                />
              ))}
            </div>
          </article>
        ))}

        {error && (
          <div
            className="flex items-start justify-between gap-3 rounded-md border border-danger-subtle bg-danger-subtle px-3 py-2"
            role="alert"
          >
            <p className="flex items-start gap-2 text-12 text-danger-primary">
              <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              {error}
            </p>
            {lastPrompt && !isStreaming && (
              <Button variant="secondary" size="base" onClick={() => onPrompt(lastPrompt)}>
                Retry
              </Button>
            )}
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
});
