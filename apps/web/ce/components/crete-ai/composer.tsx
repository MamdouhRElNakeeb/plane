/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { Send } from "lucide-react";
import { Button } from "@plane/propel/button";
import { TextArea } from "@plane/ui";
import { useCreteAI } from "@/plane-web/hooks/use-crete-ai";

type TComposerProps = {
  onPrompt: (prompt: string) => void;
};

export const CreteAIComposer = observer(function CreteAIComposer({ onPrompt }: TComposerProps) {
  const { isStreaming, isLoadingThread } = useCreteAI();
  const [prompt, setPrompt] = useState("");

  const submit = () => {
    const trimmedPrompt = prompt.trim();
    if (!trimmedPrompt || isStreaming || isLoadingThread) return;
    onPrompt(trimmedPrompt);
    setPrompt("");
  };

  return (
    <div className="border-t border-subtle-1 bg-layer-1 p-3">
      <div className="focus-within:ring-accent-primary flex items-end gap-2 rounded-lg border border-strong bg-layer-2 p-1.5 focus-within:ring-1">
        <TextArea
          aria-label="Message the AI assistant"
          placeholder="Ask the assistant…"
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          rows={1}
          mode="true-transparent"
          textAreaSize="xs"
          className="max-h-32 min-h-8 resize-none py-1.5 text-13"
          disabled={isStreaming || isLoadingThread}
        />
        <Button
          variant="primary"
          size="xl"
          className="size-8 shrink-0 px-0"
          onClick={submit}
          disabled={!prompt.trim() || isStreaming || isLoadingThread}
          aria-label="Send message"
        >
          <Send className="size-4" aria-hidden="true" />
        </Button>
      </div>
      <p className="mt-1.5 text-center text-10 text-tertiary">Enter to send · Shift+Enter for a new line</p>
    </div>
  );
});
