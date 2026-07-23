/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { Download } from "lucide-react";

import { Button } from "@plane/propel/button";
import { CloseIcon } from "@plane/propel/icons";
import { EModalPosition, EModalWidth, ModalCore, Spinner } from "@plane/ui";

import {
  CRETE_ATTACHMENT_PREVIEW_MAX_BYTES,
  isCreteAttachmentBlobSafeToPreview,
  type TCreteAttachmentPreviewKind,
} from "./crete-attachment-preview.utils";

type TCreteAttachmentPreviewModal = {
  fileName: string;
  fileURL: string;
  isOpen: boolean;
  onClose: () => void;
  previewKind: TCreteAttachmentPreviewKind;
};

export function CreteAttachmentPreviewModal(props: TCreteAttachmentPreviewModal) {
  const { fileName, fileURL, isOpen, onClose, previewKind } = props;
  const [error, setError] = useState(false);
  const [objectURL, setObjectURL] = useState<string>();

  useEffect(() => {
    if (!isOpen) return;

    const controller = new AbortController();
    let currentObjectURL: string | undefined;

    setError(false);
    setObjectURL(undefined);

    const loadPreview = async () => {
      try {
        const response = await fetch(fileURL, {
          credentials: "include",
          signal: controller.signal,
        });

        if (!response.ok) throw new Error(`Attachment preview failed with status ${response.status}`);

        const contentLength = Number(response.headers.get("content-length"));
        if (Number.isFinite(contentLength) && contentLength > CRETE_ATTACHMENT_PREVIEW_MAX_BYTES) {
          throw new Error("Attachment is too large to preview");
        }

        const blob = await response.blob();
        if (!isCreteAttachmentBlobSafeToPreview(fileName, blob.type, blob.size, previewKind)) {
          throw new Error("Attachment content type does not match its preview type");
        }

        currentObjectURL = URL.createObjectURL(blob);
        setObjectURL(currentObjectURL);
      } catch (previewError: unknown) {
        if (previewError instanceof DOMException && previewError.name === "AbortError") return;

        setError(true);
      }
    };

    void loadPreview();

    return () => {
      controller.abort();
      if (currentObjectURL) URL.revokeObjectURL(currentObjectURL);
    };
  }, [fileName, fileURL, isOpen, previewKind]);

  const handleDownload = () => window.open(fileURL, "_blank", "noopener,noreferrer");

  return (
    <ModalCore
      isOpen={isOpen}
      handleClose={onClose}
      position={EModalPosition.CENTER}
      width={EModalWidth.VIIXL}
      className="overflow-hidden"
    >
      <div className="flex h-[90vh] max-h-[90vh] flex-col">
        <div className="flex items-center justify-between gap-4 border-b border-subtle bg-surface-1 px-4 py-3">
          <p className="truncate text-13 font-medium text-primary">{fileName}</p>
          <div className="flex flex-shrink-0 items-center gap-2">
            <Button variant="secondary" size="lg" prependIcon={<Download />} onClick={handleDownload}>
              Download
            </Button>
            <button
              type="button"
              className="grid size-7 place-items-center rounded-md text-secondary hover:bg-surface-2 hover:text-primary"
              onClick={onClose}
              aria-label="Close attachment preview"
            >
              <CloseIcon className="size-4" />
            </button>
          </div>
        </div>

        <div className="flex min-h-0 flex-1 items-center justify-center bg-surface-2 p-4">
          {!objectURL && !error && <Spinner />}

          {error && (
            <div className="flex max-w-md flex-col items-center gap-3 text-center">
              <p className="text-13 font-medium text-primary">This attachment could not be previewed safely.</p>
              <p className="text-12 text-secondary">Download the file to open it with an application on your device.</p>
              <Button variant="primary" size="lg" prependIcon={<Download />} onClick={handleDownload}>
                Download
              </Button>
            </div>
          )}

          {objectURL && previewKind === "image" && (
            <img className="max-h-full max-w-full object-contain" src={objectURL} alt={fileName} />
          )}

          {objectURL && previewKind === "pdf" && (
            <object
              className="h-full w-full rounded-md bg-white"
              data={objectURL}
              type="application/pdf"
              aria-label={`Preview of ${fileName}`}
            />
          )}
        </div>
      </div>
    </ModalCore>
  );
}
