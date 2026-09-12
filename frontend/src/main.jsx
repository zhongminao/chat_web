import React from "react";
import * as ReactDOM from "react-dom/client";

const { useState, useEffect, useRef } = React;

function createId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

const TEMPERATURE_CHOICES = [0, 0.2, 0.5, 0.7, 1];

const SESSION_KEY = "chat.sessionId";

// 存储不可用时（隐私模式 / file:// / 浏览器禁用）访问 localStorage 会抛
// SecurityError，而这发生在 useState 初始化里 —— 抛出去就是整页空白。
function readStoredSessionId() {
  try {
    return window.localStorage.getItem(SESSION_KEY);
  } catch (error) {
    return null;
  }
}

function writeStoredSessionId(value) {
  try {
    window.localStorage.setItem(SESSION_KEY, value);
  } catch (error) {
    /* 存不下就只活在内存里 */
  }
}

function App() {
  const [messages, setMessages] = useState([]);
  const [sessionId, setSessionId] = useState(() => {
    const saved = readStoredSessionId();
    if (saved) {
      return saved;
    }
    const fresh = `web-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    writeStoredSessionId(fresh);
    return fresh;
  });
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [providers, setProviders] = useState([]);
  const [provider, setProvider] = useState("");
  const [modelName, setModelName] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [defaultSystemPrompt, setDefaultSystemPrompt] = useState("");
  const [isSystemPanelOpen, setIsSystemPanelOpen] = useState(false);
  const [toolsEnabled, setToolsEnabled] = useState(false);
  const [toolSystemPrompt, setToolSystemPrompt] = useState("");
  const [baseSnapshot, setBaseSnapshot] = useState("");
  const [temperature, setTemperature] = useState(null);
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [menuPane, setMenuPane] = useState("root");
  const modelMenuRef = useRef(null);
  const composerInputRef = useRef(null);

  useEffect(() => {
    fetch("/api/providers")
      .then((response) => response.json())
      .then((data) => {
        const list = data.providers || [];
        setProviders(list);
        if (data.default_system_prompt) {
          setDefaultSystemPrompt(data.default_system_prompt);
          setSystemPrompt(data.default_system_prompt);
        }
        setToolSystemPrompt(data.tool_system_prompt || "");
        setBaseSnapshot(data.default_system_prompt || "");
        if (list.length > 0) {
          setProvider(list[0].provider);
          setModelName(list[0].models?.[0]?.id || "");
        }
      })
      .catch(() => {});
  }, []);

  // 历史归服务端，这里只拿 id 把这场对话拉回来。与上面那个 effect 分开写：
  // providers 挂了不该连着历史也读不出来。404 表示这场会话还没说过话，不是错误。
  useEffect(() => {
    let cancelled = false;
    fetch(`/api/sessions/${encodeURIComponent(sessionId)}`)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (cancelled || !data) {
          return;
        }
        const restored = (data.items || []).map((item) => {
          if (item.kind === "user") {
            return { id: createId(), role: "user", content: item.content };
          }
          if (item.kind === "step") {
            return {
              id: createId(),
              role: "tool-step",
              tool: item.tool,
              result: item.result,
              ok: item.ok,
            };
          }
          return { id: createId(), role: "assistant", content: item.content };
        });
        setMessages(restored);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

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

  const canSend = input.trim() && !isLoading;

  function currentModel() {
    const entry = providers.find((item) => item.provider === provider);
    return (entry?.models || []).find((model) => model.id === modelName) || null;
  }

  // 显示值 vs 实际发送值：temperature 为 null 表示没手动设过，请求里发 null，
  // 由后端回落到 providers.yaml 里该模型的默认温度。显示值只用于 chip 和菜单。
  const modelDefaultTemperature = currentModel()?.temperature ?? 0.2;
  const shownTemperature = temperature ?? modelDefaultTemperature;

  function focusComposer() {
    composerInputRef.current?.focus();
  }

  function selectModel(nextProvider, nextModelId) {
    setProvider(nextProvider);
    setModelName(nextModelId);
    setIsMenuOpen(false);
    setMenuPane("root");
    focusComposer();
  }

  function selectTemperature(next) {
    setTemperature(next);
    setIsMenuOpen(false);
    setMenuPane("root");
    focusComposer();
  }

  async function sendMessage(rawText) {
    const content = rawText.trim();

    if (!content || isLoading) {
      return;
    }

    const userMessage = {
      id: createId(),
      role: "user",
      content,
    };

    const nextMessages = [...messages, userMessage];
    setMessages(nextMessages);
    setInput("");
    setIsLoading(true);

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        // 只发本轮新增的这一条；历史由服务端按 sessionId 重放。
        body: JSON.stringify({
          messages: [userMessage],
          sessionId,
          provider,
          model_name: modelName,
          system_prompt: systemPrompt,
          tools_enabled: toolsEnabled,
          temperature: temperature,
        }),
      });

      if (!response.ok) {
        throw new Error(`请求失败，状态码：${response.status}`);
      }

      const data = await response.json();
      const newItems = [];
      (data.steps || []).forEach((step) => {
        newItems.push({
          id: createId(),
          role: "tool-step",
          tool: step.tool,
          result: step.result,
          ok: step.ok,
        });
      });
      const assistantMessage = {
        id: createId(),
        role: "assistant",
        content: data.reply,
      };
      newItems.push(assistantMessage);

      setMessages((currentMessages) => [...currentMessages, ...newItems]);
    } catch (error) {
      // 失败只进展示：服务端也不把这次提问写进历史，所以重试是干净的。
      const assistantMessage = {
        id: createId(),
        role: "assistant",
        content: `请求失败：${error.message}`,
      };
      setMessages((currentMessages) => [...currentMessages, assistantMessage]);
    } finally {
      setIsLoading(false);
    }
  }

  function handleSubmit(event) {
    event.preventDefault();
    sendMessage(input);
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage(input);
    }
  }

  // 清空 = 换一场新会话。旧的仍留在 storage/sessions/ 里，把 localStorage 里的
  // chat.sessionId 换回旧值刷新即可找回。
  function clearMessages() {
    const fresh = `web-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    writeStoredSessionId(fresh);
    setSessionId(fresh);
    setMessages([]);
  }

  // 勾选工具模式时把工具说明拼进面板（看得见的拼接，不是后端黑盒），
  // 取消时还原成勾选前的提示词。拼好的全文随请求原样发送，后端不再自动拼接。
  function handleToolsToggle(event) {
    const next = event.target.checked;
    if (next) {
      setBaseSnapshot(systemPrompt || defaultSystemPrompt);
      setToolsEnabled(true);
      setSystemPrompt(
        toolSystemPrompt
          ? toolSystemPrompt + "\n\n" + (systemPrompt || defaultSystemPrompt)
          : systemPrompt || defaultSystemPrompt
      );
    } else {
      setToolsEnabled(false);
      setSystemPrompt(baseSnapshot);
    }
  }

  return (
    <main className="page">
      <section className="card">
        <header className="header">
          <h1>AI 聊天助手</h1>
          <button
            type="button"
            className="secondary-button"
            onClick={() => setIsSystemPanelOpen((open) => !open)}
          >
            {isSystemPanelOpen ? "收起系统提示词" : "系统提示词"}
          </button>
          <label className="tool-toggle" title="勾选后模型可调用 read/write/edit/bash 工具，面板会自动拼入工具说明">
            <input
              type="checkbox"
              checked={toolsEnabled}
              onChange={handleToolsToggle}
              disabled={isLoading}
            />
            工具模式
          </label>
          <button type="button" className="secondary-button" onClick={clearMessages}>
            清空对话
          </button>
        </header>

        {isSystemPanelOpen ? (
          <section className="system-panel">
            <div className="system-panel-title">
              <span>系统提示词（System Prompt）</span>
              <button
                type="button"
                className="link-button"
                onClick={() => {
                  setBaseSnapshot(defaultSystemPrompt);
                  setSystemPrompt(
                    toolsEnabled && toolSystemPrompt
                      ? toolSystemPrompt + "\n\n" + defaultSystemPrompt
                      : defaultSystemPrompt
                  );
                }}
                disabled={
                  !defaultSystemPrompt ||
                  systemPrompt ===
                    (toolsEnabled && toolSystemPrompt
                      ? toolSystemPrompt + "\n\n" + defaultSystemPrompt
                      : defaultSystemPrompt)
                }
              >
                恢复默认
              </button>
            </div>
            <textarea
              value={systemPrompt}
              onChange={(event) => setSystemPrompt(event.target.value)}
              rows={6}
            />
            <div className="system-panel-hint">
              修改后对下一次发送生效，不影响已有对话记录。留空则这一轮不发送系统提示词。
            </div>
          </section>
        ) : null}

        <section className="chat-box">
          {messages.map((message) => (
            <div
              key={message.id}
              className={message.role === "user" ? "message-row user-row" : "message-row"}
            >
              <div className="message-role">
                {message.role === "user" ? "我：" : "助手："}
              </div>
              <div className={message.role === "user" ? "bubble user-bubble" : "bubble"}>
                {message.role === "tool-step" ? (
                  <details className="tool-step">
                    <summary>
                      ⚙ {message.tool} {message.ok ? "" : "（执行失败）"}
                    </summary>
                    <pre>{message.result}</pre>
                  </details>
                ) : (
                  message.content
                )}
              </div>
            </div>
          ))}

          {isLoading ? (
            <div className="message-row">
              <div className="message-role">助手：</div>
              <div className="bubble loading-bubble">正在思考...</div>
            </div>
          ) : null}
        </section>

        {/* 菜单展开时给表单多挂一个类，让输入卡片外框在选择过程中保持蓝色 ——
            只靠 CSS 的 :focus-within 判断不稳，所以把状态显式写在类名上。 */}
        <form
          className={isMenuOpen ? "composer is-menu-open" : "composer"}
          onSubmit={handleSubmit}
        >
          <textarea
            ref={composerInputRef}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={handleKeyDown}
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
                  {currentModel()?.name || modelName || "选择模型"}
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
                          {currentModel()?.name || modelName}
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
                                onClick={() => selectModel(item.provider, model.id)}
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
                            onClick={() => selectTemperature(value)}
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

            <button type="submit" className="primary-button" disabled={!canSend}>
              {isLoading ? "发送中..." : "发送"}
            </button>
          </div>
        </form>
      </section>
    </main>
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
