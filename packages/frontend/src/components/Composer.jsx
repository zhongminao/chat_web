import React, { useEffect, useRef, useState } from "react";

const TEMPERATURE_CHOICES = [0, 0.2, 0.5, 0.7, 1];

// 输入卡片：textarea + 一行操作区（模型 chip + 发送按钮）。
//
// 菜单的展开状态、菜单 DOM 引用、输入框引用都**留在这里**，不往上交给 App ——
// 它们是纯界面状态，跟会话数据无关。往上放只会多一串 prop 传递。
export default function Composer({
  input,
  onInputChange,
  onSubmit,
  onKeyDown,
  isLoading,
  isStopping,
  onStop,
  canSend,
  providers,
  provider,
  modelName,
  shownTemperature,
  modelDefaultTemperature,
  onSelectModel,
  onSelectTemperature,
}) {
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [menuPane, setMenuPane] = useState("root");
  const modelMenuRef = useRef(null);
  const composerInputRef = useRef(null);

  // 点菜单外面关掉、Esc 逐级退回。挂在 document 上才能知道点到的是外面；
  // ref 挂最外层 div，让 chip 和菜单算作同一块内部区域，否则点 chip 会被判成外部点击。
  useEffect(() => {
    if (!isMenuOpen) {
      return undefined;
    }

    function handlePointerDown(event) {
      if (modelMenuRef.current && !modelMenuRef.current.contains(event.target)) {
        setIsMenuOpen(false);
        setMenuPane("root");
      }
    }

    function handleKeyDown(event) {
      if (event.key !== "Escape") {
        return;
      }
      if (menuPane === "root") {
        setIsMenuOpen(false);
      } else {
        setMenuPane("root");
      }
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isMenuOpen, menuPane]);

  // 选完把焦点送回正文输入框：焦点不出输入卡片，外框的蓝色不掉回灰色，
  // 而且下一步就能直接打字。
  function focusComposer() {
    composerInputRef.current?.focus();
  }

  function pickModel(nextProvider, nextModelId) {
    onSelectModel(nextProvider, nextModelId);
    setIsMenuOpen(false);
    setMenuPane("root");
    focusComposer();
  }

  function pickTemperature(next) {
    onSelectTemperature(next);
    setIsMenuOpen(false);
    setMenuPane("root");
    focusComposer();
  }

  const currentModel =
    (providers.find((item) => item.provider === provider)?.models || []).find(
      (model) => model.id === modelName
    ) || null;

  return (
    // 菜单展开时多挂一个类，让输入卡片外框在选择过程中保持蓝色 ——
    // 只靠 CSS 的 :focus-within 判断不稳，所以把状态显式写在类名上。
    <form
      className={isMenuOpen ? "composer is-menu-open" : "composer"}
      onSubmit={onSubmit}
    >
      <textarea
        ref={composerInputRef}
        value={input}
        onChange={(event) => onInputChange(event.target.value)}
        onKeyDown={onKeyDown}
        rows={3}
        placeholder="按 Enter 发送，Shift + Enter 换行。"
      />
      <div className="composer-actions">
        <div className="model-menu" ref={modelMenuRef}>
          <button
            type="button"
            className="model-chip"
            onClick={() => {
              setIsMenuOpen((open) => !open);
              setMenuPane("root");
            }}
            disabled={isLoading}
            aria-haspopup="menu"
            aria-expanded={isMenuOpen}
            title="选择模型与温度"
          >
            <span className="model-chip-name">
              {currentModel?.name || modelName || "选择模型"}
            </span>
            <span className="model-chip-temp">{shownTemperature}</span>
            <span className="model-chip-chevron" aria-hidden="true" />
          </button>

          {isMenuOpen ? (
            <div className="model-popup" role="menu">
              {menuPane === "root" ? (
                <div className="model-pane">
                  <button
                    type="button"
                    role="menuitem"
                    className="model-cell"
                    onClick={() => setMenuPane("model")}
                  >
                    <span className="model-cell-label">模型</span>
                    <span className="model-cell-value">
                      {currentModel?.name || modelName}
                    </span>
                    <span className="model-cell-chevron" aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    className="model-cell"
                    onClick={() => setMenuPane("temp")}
                  >
                    <span className="model-cell-label">温度</span>
                    <span className="model-cell-value">{shownTemperature}</span>
                    <span className="model-cell-chevron" aria-hidden="true" />
                  </button>
                </div>
              ) : null}

              {menuPane === "model" ? (
                <div className="model-pane">
                  {/* 手机上没有 Esc 键，少了这个按钮就退不回上一级 */}
                  <button
                    type="button"
                    className="model-back"
                    onClick={() => setMenuPane("root")}
                  >
                    <span className="model-back-arrow" aria-hidden="true" />
                    返回
                  </button>
                  {providers.map((item) => (
                    <section key={item.provider} className="model-group">
                      <div className="model-group-title">
                        {item.display_name || item.provider}
                      </div>
                      {(item.models || []).map((model) => {
                        const selected =
                          item.provider === provider && model.id === modelName;
                        return (
                          <button
                            key={model.id}
                            type="button"
                            role="menuitemradio"
                            aria-checked={selected}
                            className={
                              selected ? "model-option is-selected" : "model-option"
                            }
                            onClick={() => pickModel(item.provider, model.id)}
                          >
                            <span className="model-option-name">
                              {model.name || model.id}
                            </span>
                            {selected ? (
                              <span className="model-option-check" aria-hidden="true">
                                ✓
                              </span>
                            ) : null}
                          </button>
                        );
                      })}
                    </section>
                  ))}
                </div>
              ) : null}

              {menuPane === "temp" ? (
                <div className="model-pane">
                  <button
                    type="button"
                    className="model-back"
                    onClick={() => setMenuPane("root")}
                  >
                    <span className="model-back-arrow" aria-hidden="true" />
                    返回
                  </button>
                  <div className="model-group-title">温度</div>
                  {TEMPERATURE_CHOICES.map((value) => {
                    const selected = value === shownTemperature;
                    return (
                      <button
                        key={value}
                        type="button"
                        role="menuitemradio"
                        aria-checked={selected}
                        className={
                          selected ? "model-option is-selected" : "model-option"
                        }
                        onClick={() => pickTemperature(value)}
                      >
                        <span className="model-option-name">
                          {value}
                          {value === modelDefaultTemperature ? "（默认）" : ""}
                        </span>
                        {selected ? (
                          <span className="model-option-check" aria-hidden="true">
                            ✓
                          </span>
                        ) : null}
                      </button>
                    );
                  })}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>

        {/* 跑着的时候这个位置变成"停止"。取消是协作式的：点下去只是**请求**停止，
            真正停下要等到下一个步边界（服务端每次调模型前、每条工具调用前才检查），
            所以文案要能显示"正在停止…"—— 否则用户会以为按钮没生效。
            type="button" 是必须的：不能让它提交表单。 */}
        {isLoading ? (
          <button
            type="button"
            className="primary-button"
            onClick={onStop}
            disabled={isStopping}
          >
            {isStopping ? "正在停止…" : "停止"}
          </button>
        ) : (
          <button type="submit" className="primary-button" disabled={!canSend}>
            发送
          </button>
        )}
      </div>
    </form>
  );
}
