/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import axios, { isCancel } from "axios";
// api service
import { APIService } from "../api.service";

/**
 * Service class for handling file upload operations
 * Handles file uploads
 * @extends {APIService}
 */
export class FileUploadService extends APIService {
  private cancelSource: any;

  constructor() {
    super("");
  }

  /**
   * Uploads a file to the specified signed URL
   * @param {string} url - The URL to upload the file to
   * @param {FormData | File} data - The upload payload
   * @returns {Promise<void>} Promise resolving to void
   * @throws {Error} If the request fails
   */
  async uploadFile(url: string, data: FormData | File): Promise<void> {
    // eslint-disable-next-line import/no-named-as-default-member
    this.cancelSource = axios.CancelToken.source();
    const request = data instanceof FormData ? this.post.bind(this) : this.put.bind(this);
    return request(url, data, {
      headers: {
        "Content-Type": data instanceof FormData ? "multipart/form-data" : data.type,
      },
      cancelToken: this.cancelSource.token,
      withCredentials: false,
    })
      .then((response) => response?.data)
      .catch((error) => {
        if (isCancel(error)) {
          console.log(error.message);
        } else {
          throw error?.response?.data;
        }
      });
  }

  /**
   * Cancels the upload
   */
  cancelUpload() {
    this.cancelSource.cancel("Upload canceled");
  }
}
