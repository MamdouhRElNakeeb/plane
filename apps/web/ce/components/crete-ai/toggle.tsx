/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useParams } from "next/navigation";
import { AiIcon } from "@plane/propel/icons";
import { Tooltip } from "@plane/propel/tooltip";
import { cn } from "@plane/utils";
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
import { useCreteAI } from "@/plane-web/hooks/use-crete-ai";

export const CreteAIToggle = observer(function CreteAIToggle() {
  const { projectId, issueId, workItem } = useParams();
  const {
    issue: { getIssueById, getIssueIdByIdentifier },
  } = useIssueDetail();
  const { isPanelOpen, togglePanel } = useCreteAI();
  const resolvedIssueId = issueId?.toString() ?? getIssueIdByIdentifier(workItem?.toString() ?? "");
  const resolvedProjectId = projectId?.toString() ?? getIssueById(resolvedIssueId ?? "")?.project_id;

  return (
    <Tooltip tooltipContent={isPanelOpen ? "Close Crete AI" : "Open Crete AI"} position="bottom">
      <button
        type="button"
        className={cn(
          "focus-visible:ring-accent-primary grid size-8 place-items-center rounded-md text-icon-tertiary hover:bg-layer-transparent-hover hover:text-icon-secondary focus-visible:ring-1 focus-visible:outline-none",
          {
            "bg-layer-transparent-selected text-icon-primary": isPanelOpen,
          }
        )}
        onClick={() =>
          togglePanel({
            projectId: resolvedProjectId,
            issueId: resolvedIssueId,
          })
        }
        aria-label={isPanelOpen ? "Close Crete AI assistant" : "Open Crete AI assistant"}
        aria-pressed={isPanelOpen}
      >
        <AiIcon className="size-5" />
      </button>
    </Tooltip>
  );
});
