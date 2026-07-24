/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// store
import { CoreRootStore } from "@/store/root.store";
import type { ICreteAIStore } from "./crete-ai.store";
import { CreteAIStore } from "./crete-ai.store";
import type { ITimelineStore } from "./timeline";
import { TimeLineStore } from "./timeline";

export class RootStore extends CoreRootStore {
  creteAI: ICreteAIStore;
  timelineStore: ITimelineStore;

  constructor() {
    super();

    this.creteAI = new CreteAIStore(this);
    this.timelineStore = new TimeLineStore(this);
  }

  override resetOnSignOut() {
    this.creteAI.reset();
    super.resetOnSignOut();
    this.creteAI = new CreteAIStore(this);
    this.timelineStore = new TimeLineStore(this);
  }
}
