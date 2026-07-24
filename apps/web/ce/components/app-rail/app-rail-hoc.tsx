/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// hoc/withDockItems.tsx
import React from "react";
import { observer } from "mobx-react";
import { useParams } from "next/navigation";
import { AiIcon, PlaneNewIcon } from "@plane/propel/icons";
import type { AppSidebarItemData } from "@/components/sidebar/sidebar-item";
import { useWorkspacePaths } from "@/hooks/use-workspace-paths";

type WithDockItemsProps = {
  dockItems: (AppSidebarItemData & { shouldRender: boolean })[];
};

export function withDockItems<P extends WithDockItemsProps>(WrappedComponent: React.ComponentType<P>) {
  const ComponentWithDockItems = observer(function ComponentWithDockItems(props: Omit<P, keyof WithDockItemsProps>) {
    const { workspaceSlug } = useParams();
    const { isProjectsPath, isNotificationsPath, isAiPath } = useWorkspacePaths();

    const dockItems: (AppSidebarItemData & { shouldRender: boolean })[] = [
      {
        label: "Projects",
        icon: <PlaneNewIcon className="size-5" />,
        href: `/${workspaceSlug}/`,
        isActive: isProjectsPath && !isNotificationsPath,
        shouldRender: true,
      },
      {
        label: "Crete AI",
        icon: <AiIcon className="size-5" />,
        href: `/${workspaceSlug}/pi-chat`,
        isActive: isAiPath,
        shouldRender: true,
      },
    ];

    return <WrappedComponent {...(props as P)} dockItems={dockItems} />;
  });

  return ComponentWithDockItems;
}
