/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect } from "react";
import { observer } from "mobx-react";
import { CustomSelect } from "@plane/ui";
import { useProject } from "@/hooks/store/use-project";
import { useWorkspace } from "@/hooks/store/use-workspace";
import { useCreteAI } from "@/plane-web/hooks/use-crete-ai";
import type { TCreteAIContextType } from "@/plane-web/types/crete-ai";
import { CRETE_AI_CONTEXT_LABELS, CRETE_AI_CONTEXT_TYPES } from "@/plane-web/types/crete-ai";

type TContextSelectorProps = {
  workspaceSlug: string;
};

export const CreteAIContextSelector = observer(function CreteAIContextSelector({
  workspaceSlug,
}: TContextSelectorProps) {
  const { currentWorkspace } = useWorkspace();
  const { workspaceProjectIds, getProjectById, fetchProjects } = useProject();
  const { contextType, selectedProjectId, selectedIssueId, setContextType, setProjectId } = useCreteAI();

  useEffect(() => {
    if (currentWorkspace && workspaceProjectIds === undefined) void fetchProjects(workspaceSlug);
  }, [currentWorkspace, fetchProjects, workspaceProjectIds, workspaceSlug]);

  const selectedProject = getProjectById(selectedProjectId);

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-subtle-1 bg-layer-1 px-3 py-2">
      <span className="text-11 font-medium text-tertiary">Context</span>
      <CustomSelect
        value={contextType}
        label={CRETE_AI_CONTEXT_LABELS[contextType]}
        onChange={(value: TCreteAIContextType) => setContextType(value)}
        buttonClassName="min-w-24"
        placement="bottom-start"
      >
        {CRETE_AI_CONTEXT_TYPES.map((type) => (
          <CustomSelect.Option key={type} value={type}>
            {CRETE_AI_CONTEXT_LABELS[type]}
          </CustomSelect.Option>
        ))}
      </CustomSelect>

      {(contextType === "project" || contextType === "work_item") && (
        <CustomSelect
          value={selectedProjectId}
          label={selectedProject?.name ?? "Choose project"}
          onChange={(value: string) => setProjectId(value)}
          buttonClassName="max-w-48"
          placement="bottom-start"
        >
          {(workspaceProjectIds ?? []).map((projectId) => {
            const project = getProjectById(projectId);
            if (!project) return null;
            return (
              <CustomSelect.Option key={project.id} value={project.id}>
                <span className="max-w-44 truncate">{project.name}</span>
              </CustomSelect.Option>
            );
          })}
        </CustomSelect>
      )}

      {contextType === "work_item" && (
        <span
          className="max-w-44 truncate rounded border border-subtle-1 bg-layer-2 px-2 py-1 text-11 text-secondary"
          title={selectedIssueId ?? "Open a work item to bind this context"}
        >
          {selectedIssueId ? `Work item ${selectedIssueId}` : "No work item selected"}
        </span>
      )}
    </div>
  );
});
