/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { PageHead } from "@/components/core/page-title";
import { CreteAIChatRoot } from "@/plane-web/components/crete-ai";
import type { Route } from "./+types/page";

export default function CreteAIPage({ params }: Route.ComponentProps) {
  return (
    <>
      <PageHead title="Crete AI" />
      <main className="size-full overflow-hidden rounded-md border border-subtle-1 bg-surface-1">
        <CreteAIChatRoot workspaceSlug={params.workspaceSlug} variant="page" />
      </main>
    </>
  );
}
