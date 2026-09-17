// 样式：packages/chat/src/chat/static/styles/06-responsive-overlay.css（.picker-* / .modal）
// 窄屏（手机）覆盖统一在 06-responsive-overlay.css，改小屏表现去那里找。
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
  // 新建工作区：在当前位置下开一个新目录并登记。不该逼用户先去终端 mkdir。
  const [creating, setCreating] = useState(false);
  const [nameDraft, setNameDraft] = useState("");

  // 每次打开都从 home 重新开始，不记得上次走到哪 —— 记着反而容易误选。
  useEffect(() => {
    if (!open) {
      return;
    }
    setError("");
    setPath("");
    setCreating(false);
    setNameDraft("");
  }, [open]);

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    let cancelled = false;
    browseDirectories(path)
      .then((data) => {
        if (cancelled) {
          return;
        }
        // 服务端回了个不认识的形状就当读不出来。别让 entries 缺失把整个界面带崩 ——
        // 这里是「少个字段」，不该表现成白屏。
        if (!data || typeof data.path !== "string") {
          setListing(null);
          setError("这个目录读不出来");
          return;
        }
        setListing({ ...data, entries: Array.isArray(data.entries) ? data.entries : [] });
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
      await onConfirm({ root: listing.path });
    } catch (confirmError) {
      setError(confirmError.message);
      setBusy(false);
    }
  }

  async function submitCreate(event) {
    event.preventDefault();
    const name = nameDraft.trim();
    if (!name || !listing?.path) {
      return;
    }
    setBusy(true);
    try {
      await onConfirm({ parent: listing.path, name });
    } catch (createError) {
      setError(createError.message);
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

        {/* 左下角是"新建工作区"，右下角是取消/确认 —— 两类动作分开摆，
            免得"建一个新的"和"就要这个"挨在一起被误点。 */}
        <div className="modal-actions">
          <div className="modal-actions-left">
            {creating ? (
              <form className="picker-create" onSubmit={submitCreate}>
                <input
                  autoFocus
                  value={nameDraft}
                  onChange={(event) => setNameDraft(event.target.value)}
                  placeholder="新目录名"
                  aria-label="新工作区目录名"
                />
                <button type="submit" className="secondary-button" disabled={busy || !nameDraft.trim()}>
                  建
                </button>
              </form>
            ) : (
              <button
                type="button"
                className="secondary-button"
                onClick={() => setCreating(true)}
                disabled={!listing?.path}
              >
                新建工作区
              </button>
            )}
          </div>

          <div className="modal-actions-right">
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
    </div>
  );
}
