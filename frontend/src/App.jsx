import React, { useEffect, useState } from "react";

import { fetchProviders, fetchSessionItems, fetchSessions, sendChat } from "./api";
import { newSessionId, readStoredSessionId, writeStoredSessionId } from "./session";
import { readStored, writeStored } from "./storage";
import Composer from "./components/Composer";
import MessageList from "./components/MessageList";
import SessionSidebar from "./components/SessionSidebar";
import SystemPanel from "./components/SystemPanel";

const SIDEBAR_KEY = "chat.sidebarCollapsed";

function createId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function toDisplayItems(items) {
  return (items || []).map((item) => {
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
}

export default function App() {
  const [messages, setMessages] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [workspace, setWorkspace] = useState("");
  const [sessionId, setSessionId] = useState(() => {
    const saved = readStoredSessionId();
    if (saved) {
      return saved;
    }
    const fresh = newSessionId();
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
  // 侧栏收起状态存 localStorage —— 这是个界面偏好，刷新后不该跳回去。
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => readStored(SIDEBAR_KEY) === "1"
  );

  function toggleSidebar() {
    setSidebarCollapsed((collapsed) => {
      writeStored(SIDEBAR_KEY, collapsed ? "0" : "1");
      return !collapsed;
    });
  }

  // 侧栏数据。每轮对话结束后要重取一次 —— 标题和轮数都跟着变。
  function refreshSessions() {
    fetchSessions()
      .then((data) => {
        setSessions(data.sessions || []);
        setWorkspace(data.workspace || "");
      })
      .catch(() => {});
  }

  useEffect(() => {
    fetchProviders()
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

  useEffect(() => {
    refreshSessions();
  }, []);

  // 历史归服务端，这里只拿 id 把这场对话拉回来。与上面那个 effect 分开写：
  // providers 挂了不该连着历史也读不出来。
  useEffect(() => {
    let cancelled = false;
    fetchSessionItems(sessionId)
      .then((data) => {
        if (cancelled || !data) {
          return;
        }
        setMessages(toDisplayItems(data.items));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const currentModel =
    (providers.find((item) => item.provider === provider)?.models || []).find(
      (model) => model.id === modelName
    ) || null;

  // 显示值 vs 实际发送值：temperature 为 null 表示没手动设过，请求里发 null，
  // 由后端回落到 providers.yaml 里该模型的默认温度。显示值只用于 chip 和菜单。
  const modelDefaultTemperature = currentModel?.temperature ?? 0.2;
  const shownTemperature = temperature ?? modelDefaultTemperature;
  const canSend = Boolean(input.trim()) && !isLoading;

  async function sendMessage(rawText) {
    const content = rawText.trim();

    if (!content || isLoading) {
      return;
    }

    const userMessage = { id: createId(), role: "user", content };
    setMessages((currentMessages) => [...currentMessages, userMessage]);
    setInput("");
    setIsLoading(true);

    try {
      // 只发本轮新增的这一条；历史由服务端按 sessionId 重放。
      const data = await sendChat({
        messages: [{ role: "user", content }],
        sessionId,
        provider,
        model_name: modelName,
        system_prompt: systemPrompt,
        tools_enabled: toolsEnabled,
        temperature,
      });

      const newItems = (data.steps || []).map((step) => ({
        id: createId(),
        role: "tool-step",
        tool: step.tool,
        result: step.result,
        ok: step.ok,
      }));
      newItems.push({ id: createId(), role: "assistant", content: data.reply });
      setMessages((currentMessages) => [...currentMessages, ...newItems]);
      refreshSessions();   // 标题/轮数变了，侧栏跟着更新
    } catch (error) {
      // 失败只进展示：服务端也不把这次提问写进历史，所以重试是干净的。
      setMessages((currentMessages) => [
        ...currentMessages,
        { id: createId(), role: "assistant", content: `请求失败：${error.message}` },
      ]);
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

  // 清空 = 换一场新会话。旧的仍留在 storage/sessions/ 里，随时能从侧栏点回去。
  function clearMessages() {
    const fresh = newSessionId();
    writeStoredSessionId(fresh);
    setSessionId(fresh);
    setMessages([]);
  }

  // 从侧栏切到另一场对话：换 id 即可，历史那个 effect 会把它拉回来。
  function selectSession(nextSessionId) {
    if (nextSessionId === sessionId) {
      return;
    }
    writeStoredSessionId(nextSessionId);
    setSessionId(nextSessionId);
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

  function restoreDefaultSystemPrompt() {
    setBaseSnapshot(defaultSystemPrompt);
    setSystemPrompt(
      toolsEnabled && toolSystemPrompt
        ? toolSystemPrompt + "\n\n" + defaultSystemPrompt
        : defaultSystemPrompt
    );
  }

  return (
    <main className="page">
      <SessionSidebar
        workspace={workspace}
        sessions={sessions}
        activeId={sessionId}
        collapsed={sidebarCollapsed}
        onToggle={toggleSidebar}
        onSelect={selectSession}
        onNew={clearMessages}
      />

      <section className="card">
        <header className="header">
          <button
            type="button"
            className="secondary-button"
            onClick={() => setIsSystemPanelOpen((open) => !open)}
          >
            {isSystemPanelOpen ? "收起系统提示词" : "系统提示词"}
          </button>
          <label
            className="tool-toggle"
            title="勾选后模型可调用 read/write/edit/bash 工具，面板会自动拼入工具说明"
          >
            <input
              type="checkbox"
              checked={toolsEnabled}
              onChange={handleToolsToggle}
              disabled={isLoading}
            />
            工具模式
          </label>
        </header>

        {isSystemPanelOpen ? (
          <SystemPanel
            systemPrompt={systemPrompt}
            onSystemPromptChange={setSystemPrompt}
            defaultSystemPrompt={defaultSystemPrompt}
            toolsEnabled={toolsEnabled}
            toolSystemPrompt={toolSystemPrompt}
            onRestoreDefault={restoreDefaultSystemPrompt}
          />
        ) : null}

        <MessageList messages={messages} isLoading={isLoading} />

        <Composer
          input={input}
          onInputChange={setInput}
          onSubmit={handleSubmit}
          onKeyDown={handleKeyDown}
          isLoading={isLoading}
          canSend={canSend}
          providers={providers}
          provider={provider}
          modelName={modelName}
          shownTemperature={shownTemperature}
          modelDefaultTemperature={modelDefaultTemperature}
          onSelectModel={(nextProvider, nextModelId) => {
            setProvider(nextProvider);
            setModelName(nextModelId);
          }}
          onSelectTemperature={setTemperature}
        />
      </section>
    </main>
  );
}
