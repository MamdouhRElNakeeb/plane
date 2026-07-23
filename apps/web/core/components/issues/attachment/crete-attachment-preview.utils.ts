/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

export type TCreteAttachmentPreviewKind = "image" | "pdf";

export const CRETE_ATTACHMENT_PREVIEW_MAX_BYTES = 25 * 1024 * 1024;

const PREVIEWABLE_FILE_TYPES: Record<string, { extensions: string[]; kind: TCreteAttachmentPreviewKind }> = {
  "application/pdf": {
    extensions: ["pdf"],
    kind: "pdf",
  },
  "image/gif": {
    extensions: ["gif"],
    kind: "image",
  },
  "image/jpeg": {
    extensions: ["jpeg", "jpg"],
    kind: "image",
  },
  "image/png": {
    extensions: ["png"],
    kind: "image",
  },
  "image/webp": {
    extensions: ["webp"],
    kind: "image",
  },
};

const normalizeMimeType = (mimeType: string | undefined): string => mimeType?.split(";")[0]?.trim().toLowerCase() ?? "";

const getFileExtension = (fileName: string): string => {
  const extension = fileName.split(".").pop();

  return extension && extension !== fileName ? extension.toLowerCase() : "";
};

export const getCreteAttachmentPreviewKind = (
  fileName: string,
  mimeType: string | undefined,
  fileSize?: number
): TCreteAttachmentPreviewKind | undefined => {
  if (
    fileSize !== undefined &&
    (!Number.isFinite(fileSize) || fileSize < 0 || fileSize > CRETE_ATTACHMENT_PREVIEW_MAX_BYTES)
  )
    return undefined;

  const previewType = PREVIEWABLE_FILE_TYPES[normalizeMimeType(mimeType)];

  if (!previewType || !previewType.extensions.includes(getFileExtension(fileName))) return undefined;

  return previewType.kind;
};

export const isCreteAttachmentBlobSafeToPreview = (
  fileName: string,
  blobType: string,
  blobSize: number,
  expectedKind: TCreteAttachmentPreviewKind
): boolean => getCreteAttachmentPreviewKind(fileName, blobType, blobSize) === expectedKind;
