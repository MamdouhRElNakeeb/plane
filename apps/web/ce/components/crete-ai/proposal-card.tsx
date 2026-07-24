/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { CheckCircle2, CircleAlert } from "lucide-react";
import { Button } from "@plane/propel/button";
import type { ICreteAIProposal } from "@/plane-web/types/crete-ai";

const ACTION_LABELS: Record<ICreteAIProposal["type"], string> = {
  create_comment: "Create comment",
  edit_issue_description: "Edit work item description",
  create_subtask: "Create sub-work item",
};

const formatValue = (value: unknown): string => {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (value === null || value === undefined) return "Not set";
  try {
    return JSON.stringify(value);
  } catch {
    return "Structured value";
  }
};

type TProposalCardProps = {
  proposal: ICreteAIProposal;
  onConfirm: (proposalId: string) => void;
};

export function CreteAIProposalCard({ proposal, onConfirm }: TProposalCardProps) {
  const payloadEntries = Object.entries(proposal.payload).slice(0, 6);

  return (
    <section
      className="mt-3 rounded-lg border border-strong bg-layer-2 p-3"
      aria-label={`${ACTION_LABELS[proposal.type]} proposal`}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-12 font-semibold text-primary">{ACTION_LABELS[proposal.type]}</p>
          <p className="mt-0.5 text-11 text-tertiary">Review this proposed action before applying it.</p>
        </div>
        {proposal.status === "completed" && (
          <CheckCircle2 className="size-4 shrink-0 text-success-primary" aria-hidden="true" />
        )}
      </div>

      {payloadEntries.length > 0 && (
        <dl className="mt-3 space-y-1.5">
          {payloadEntries.map(([key, value]) => (
            <div key={key} className="grid grid-cols-[minmax(5rem,0.35fr)_1fr] gap-2 text-11">
              <dt className="truncate font-medium text-tertiary">{key.replaceAll("_", " ")}</dt>
              <dd className="max-h-48 overflow-auto break-words whitespace-pre-wrap text-secondary">
                {formatValue(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {proposal.status === "completed" && (
        <div className="mt-3 text-11 text-success-primary">
          <p className="flex items-center gap-1.5">
            <CheckCircle2 className="size-3.5" aria-hidden="true" />
            Action completed
          </p>
          {proposal.result !== undefined && (
            <p className="mt-1 line-clamp-3 break-words text-tertiary">Result: {formatValue(proposal.result)}</p>
          )}
        </div>
      )}
      {proposal.status === "error" && (
        <p className="mt-3 flex items-start gap-1.5 text-11 text-danger-primary" role="alert">
          <CircleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          {proposal.error ?? "This action could not be completed."}
        </p>
      )}

      {proposal.status !== "completed" && (
        <div className="mt-3 flex justify-end">
          <Button
            variant="primary"
            size="lg"
            loading={proposal.status === "confirming"}
            disabled={proposal.status === "confirming"}
            onClick={() => onConfirm(proposal.id)}
            aria-label={`Confirm ${ACTION_LABELS[proposal.type].toLowerCase()}`}
          >
            {proposal.status === "error" ? "Try confirmation again" : "Confirm action"}
          </Button>
        </div>
      )}
    </section>
  );
}
