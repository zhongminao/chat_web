import React, { useEffect, useState } from "react";

import { browseDirectories } from "../api";
import { IconFolderOpen16 } from "../icons";

// 挑目录。存在的理由：让用户手打绝对路径不叫交互。
//
// 上游用的是 ui-directory-picker 那两个包（原生 + 浏览两种），这里做最小可用版：
// 列子目录、能往上走、点「选这个目录」即登记。只列目录不列文件 —— 工作区是目录。
export default function DirectoryPicker({ open, onClose, onConfirm }) {
  const [path, setPath] = useState("");
  const [listing, setListing] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // 每次打开都从 home 重新开始，不记得上次走到哪 —— 记着反而容易误选。
  useEffect(() => {
    if (!open) {
      return;
    }
    setError("");
    setPath("");
  }, [open]);

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    let cancelled = false;
    browseDirectories(path)
      .then((data) => {
        if (!cancelled) {
          setListing(data);
        }
      })
      .catch((browseError) => {
        if (!cancelled) {
          setError(browseError.message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [open, path]);

  if (!open) {
    return null;
  }

  async function confirm() {
    if (!listing?.path) {
      return;
    }
    setBusy(true);
    try {
      await onConfirm(listing.path);
    } catch (confirmError) {
      setError(confirmError.message);
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
        <div className="modal-title">添加工作区</div>
        <div className="picker-path" title={listing?.path || ""}>
          {listing?.path || "读取中…"}
        </div>

        <div className="picker-list">
          {listing?.parent ? (
            <button type="button" className="picker-row" onClick={() => setPath(listing.parent)}>
              <span className="picker-row-icon">↑</span>
              <span className="picker-row-name">上一级</span>
            </button>
          ) : null}
          {(listing?.entries || []).map((entry) => (
            <button
              key={entry.path}
              type="button"
              className="picker-row"
              onClick={() => setPath(entry.path)}
            >
              <span className="picker-row-icon">
                <IconFolderOpen16 size={16} />
              </span>
              <span className="picker-row-name">{entry.name}</span>
            </button>
          ))}
          {listing && listing.entries.length === 0 ? (
            <div className="picker-empty">这里没有子目录</div>
          ) : null}
        </div>

        {error ? <div className="modal-error">{error}</div> : null}

        <div className="modal-actions">
          <button type="button" className="secondary-button" onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            className="primary-button"
            onClick={confirm}
            disabled={busy || !listing?.path}
          >
            选这个目录
          </button>
        </div>
      </div>
    </div>
  );
}
