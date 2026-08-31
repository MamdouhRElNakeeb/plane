/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useCallback } from "react";
import type { FileRejection } from "react-dropzone";
import { useDropzone } from "react-dropzone";
import { Paperclip, X } from "lucide-react";
import { useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
// hooks
import { useFileSize } from "@/plane-web/hooks/use-file-size";

type Props = {
  disabled?: boolean;
  files: File[];
  onChange: (files: File[]) => void;
};

const getFileKey = (file: File) => `${file.name}-${file.size}-${file.lastModified}`;

export function IssueModalQueuedAttachments(props: Props) {
  const { disabled = false, files, onChange } = props;
  const { t } = useTranslation();
  const { maxFileSize } = useFileSize();

  const onDrop = useCallback(
    (acceptedFiles: File[], rejectedFiles: FileRejection[]) => {
      if (acceptedFiles.length > 0) {
        const existingFileKeys = new Set(files.map(getFileKey));
        const uniqueAcceptedFiles = acceptedFiles.filter((file) => {
          const fileKey = getFileKey(file);
          if (existingFileKeys.has(fileKey)) return false;
          existingFileKeys.add(fileKey);
          return true;
        });
        onChange([...files, ...uniqueAcceptedFiles]);
      }

      if (rejectedFiles.length > 0) {
        setToast({
          type: TOAST_TYPE.ERROR,
          title: t("error"),
          message: t("attachment.file_size_limit", { size: maxFileSize / 1024 / 1024 }),
        });
      }
    },
    [files, maxFileSize, onChange, t]
  );

  const { getInputProps, getRootProps, isDragActive } = useDropzone({
    disabled,
    maxSize: maxFileSize,
    multiple: true,
    onDrop,
  });

  return (
    <div className="space-y-2">
      <div
        {...getRootProps()}
        className={`flex min-h-9 items-center justify-center gap-2 rounded-md border border-dashed px-3 py-2 text-caption-sm-regular ${
          isDragActive
            ? "border-accent-strong bg-accent-primary/10 text-accent-primary"
            : "border-subtle text-secondary"
        } ${disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer hover:bg-surface-2"}`}
      >
        <input {...getInputProps()} />
        <Paperclip className="size-3.5 flex-shrink-0" aria-hidden="true" />
        <span>{isDragActive ? t("attachment.drag_and_drop") : t("common.attach")}</span>
      </div>

      {files.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {files.map((file) => (
            <div
              key={getFileKey(file)}
              className="flex max-w-full items-center gap-1.5 rounded bg-surface-2 px-2 py-1 text-caption-sm-regular text-secondary"
            >
              <span className="max-w-64 truncate" title={file.name}>
                {file.name}
              </span>
              <button
                type="button"
                className="flex-shrink-0 rounded-sm text-tertiary hover:text-primary"
                onClick={(event) => {
                  event.stopPropagation();
                  onChange(files.filter((queuedFile) => getFileKey(queuedFile) !== getFileKey(file)));
                }}
                disabled={disabled}
                aria-label={`${t("remove")} ${file.name}`}
              >
                <X className="size-3.5" aria-hidden="true" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
