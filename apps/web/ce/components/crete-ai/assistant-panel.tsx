/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useParams } from "next/navigation";
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
import { useCreteAI } from "@/plane-web/hooks/use-crete-ai";
import { CreteAIChatRoot } from "./chat-root";

export const CreteAIAssistantPanel = observer(function CreteAIAssistantPanel() {
  const { workspaceSlug, projectId, issueId, workItem } = useParams();
  const {
    issue: { getIssueById, getIssueIdByIdentifier },
  } = useIssueDetail();
  const { isPanelOpen, setPanelOpen } = useCreteAI();
  const resolvedIssueId = issueId?.toString() ?? getIssueIdByIdentifier(workItem?.toString() ?? "");
  const resolvedProjectId = projectId?.toString() ?? getIssueById(resolvedIssueId ?? "")?.project_id;

  if (!isPanelOpen || !workspaceSlug) return null;

  return (
    <div
      className="fixed inset-0 z-40 flex min-w-0 shrink-0 overflow-hidden bg-surface-1 shadow-raised-200 md:relative md:inset-auto md:z-auto md:mr-2 md:mb-2 md:h-auto md:w-[25rem] md:rounded-md md:border md:border-subtle-1"
      role="dialog"
      aria-label="Crete AI assistant"
    >
      <CreteAIChatRoot
        workspaceSlug={workspaceSlug.toString()}
        routeContext={{
          projectId: resolvedProjectId,
          issueId: resolvedIssueId,
        }}
        variant="panel"
        onClose={() => setPanelOpen(false)}
      />
    </div>
  );
});
