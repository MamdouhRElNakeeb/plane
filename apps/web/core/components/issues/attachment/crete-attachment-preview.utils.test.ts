/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { describe, expect, it } from "vitest";

import {
  CRETE_ATTACHMENT_PREVIEW_MAX_BYTES,
  getCreteAttachmentPreviewKind,
  isCreteAttachmentBlobSafeToPreview,
} from "./crete-attachment-preview.utils";

describe("getCreteAttachmentPreviewKind", () => {
  it.each([
    ["image.png", "image/png", "image"],
    ["photo.JPG", "image/jpeg", "image"],
    ["animation.gif", "image/gif", "image"],
    ["graphic.webp", "image/webp; charset=binary", "image"],
    ["document.pdf", "application/pdf", "pdf"],
  ])("allows %s with %s", (fileName, mimeType, expectedKind) => {
    expect(getCreteAttachmentPreviewKind(fileName, mimeType)).toBe(expectedKind);
  });

  it.each([
    ["vector.svg", "image/svg+xml"],
    ["page.html", "text/html"],
    ["image.png", "text/html"],
    ["image.svg", "image/png"],
    ["document.txt", "application/pdf"],
    ["image.png", undefined],
  ])("rejects %s with %s", (fileName, mimeType) => {
    expect(getCreteAttachmentPreviewKind(fileName, mimeType)).toBeUndefined();
  });

  it("rejects files larger than the preview limit", () => {
    expect(
      getCreteAttachmentPreviewKind("image.png", "image/png", CRETE_ATTACHMENT_PREVIEW_MAX_BYTES + 1)
    ).toBeUndefined();
  });
});

describe("isCreteAttachmentBlobSafeToPreview", () => {
  it("accepts a matching response content type", () => {
    expect(isCreteAttachmentBlobSafeToPreview("image.png", "image/png", 1024, "image")).toBe(true);
  });

  it("rejects a response content type that differs from the expected preview", () => {
    expect(isCreteAttachmentBlobSafeToPreview("image.png", "application/pdf", 1024, "image")).toBe(false);
  });

  it("rejects responses larger than the preview limit", () => {
    expect(
      isCreteAttachmentBlobSafeToPreview("image.png", "image/png", CRETE_ATTACHMENT_PREVIEW_MAX_BYTES + 1, "image")
    ).toBe(false);
  });
});
