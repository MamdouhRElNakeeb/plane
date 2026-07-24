/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { layout, route } from "@react-router/dev/routes";
import type { RouteConfigEntry } from "@react-router/dev/routes";

export const extendedRoutes: RouteConfigEntry[] = [
  layout("./(all)/layout.tsx", [
    layout("./(all)/[workspaceSlug]/layout.tsx", [
      route(":workspaceSlug/pi-chat", "./(all)/[workspaceSlug]/pi-chat/page.tsx"),
      route(":workspaceSlug/pi-chat/:threadId", "./(all)/[workspaceSlug]/pi-chat/[threadId]/page.tsx"),
    ]),
  ]),
];
