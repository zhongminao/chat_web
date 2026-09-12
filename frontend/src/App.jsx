import React, { useEffect, useState } from "react";

import {
  createWorkspace,
  fetchProviders,
  fetchSessionItems,
  fetchSessions,
  fetchWorkspaces,
  sendChat,
  updateSessionSettings,
} from "./api";
import { newSessionId, readStoredSessionId, writeStoredSessionId } from "./session";
import { readStored, writeStored } from "./storage";
import Composer from "./components/Composer";
import MessageList from "./components/MessageList";
import SessionSidebar from "./components/SessionSidebar";
import SystemPanel from "./components/SystemPanel";

const SIDEBAR_KEY = "chat.sidebarCollapsed";
const WORKSPACE_KEY = "chat.workspaceId";

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
  const [workspaceId, setWorkspaceId] = useState(() => readStored(WORKSPACE_KEY) || "");
  const [workspaces, setWorkspaces] = useState([]);
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
  // 从会话恢复设置要等默认提示词到齐才能拼系统提示词，见下面那个 effect。
  const [loadedSettings, setLoadedSettings] = useState(null);
  const [providersReady, setProvidersReady] = useState(false);

  function toggleSidebar() {
    setSidebarCollapsed((collapsed) => {
      writeStored(SIDEBAR_KEY, collapsed ? "0" : "1");
      return !collapsed;
    });
  }

  // 侧栏数据。每轮对话结束后要重取一次 —— 标题和轮数都跟着变。
  function refreshSessions(targetWorkspaceId = workspaceId) {
    return fetchSessions(targetWorkspaceId)
      .then((data) => {
        setSessions(data.sessions || []);
        // 服务端不认识客户端给的 id 时会回落到默认工作区，以它为准，别各说各的。
        if (data.workspace?.id && data.workspace.id !== targetWorkspaceId) {
          writeStored(WORKSPACE_KEY, data.workspace.id);
          setWorkspaceId(data.workspace.id);
          return data.workspace.id;
        }
        return targetWorkspaceId;
      })
      .catch(() => null);
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
        setProvidersReady(true);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    refreshSessions(workspaceId);
  }, [workspaceId]);

  // 工作区登记表。默认工作区由服务端决定（客户端本地存的 id 可能已经被删）。
  function refreshWorkspaces() {
    return fetchWorkspaces()
      .then((data) => {
        setWorkspaces(data.workspaces || []);
        if (!readStored(WORKSPACE_KEY) && data.default) {
          writeStored(WORKSPACE_KEY, data.default);
          setWorkspaceId(data.default);
        }
        return data;
      })
      .catch(() => null);
  }

  useEffect(() => {
    refreshWorkspaces();
  }, []);

  // 历史归服务端，这里只拿 id 把这场对话拉回来。与上面那个 effect 分开写：
  // providers 挂了不该连着历史也读不出来。
  useEffect(() => {
    let cancelled = false;
    fetchSessionItems(sessionId)
      .then((data) => {
        if (cancelled) {
          return;
        }
        // 新会话（404）拿不到东西：清空展示，并把设置复位成默认。
        setMessages(toDisplayItems(data?.items));
        setLoadedSettings(data?.settings || {});
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  // 把会话级设置落到界面上。**必须等默认提示词到齐**再改 toolsEnabled ——
  // 改它会连带拼接系统提示词，而那时 defaultSystemPrompt 还是空的，
  // 拼出来就只剩工具说明那半截了。
  useEffect(() => {
    if (!providersReady || loadedSettings === null) {
      return;
    }
    applyToolsEnabled(Boolean(loadedSettings.toolsEnabled));
    setLoadedSettings(null);
  }, [providersReady, loadedSettings]);

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
      // workspaceId 只对新会话生效 —— 老会话以服务端 header 里记的为准。
      const data = await sendChat({
        messages: [{ role: "user", content }],
        sessionId,
        workspaceId,
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

  // 从侧栏切到另一场对话：换 id 即可，历史那个 effect 会把它拉回来
  // （连同这场对话自己的设置 —— 是否使用工具是跟着对话走的）。
  function selectSession(nextSessionId) {
    if (nextSessionId === sessionId) {
      return;
    }
    writeStoredSessionId(nextSessionId);
    setSessionId(nextSessionId);
    setMessages([]);
  }

  // 切工作区 = 开一场新对话。一个对话只属于一个工作区，绑定之后不该半路改。
  function selectWorkspace(nextWorkspaceId) {
    if (nextWorkspaceId === workspaceId) {
      return;
    }
    writeStored(WORKSPACE_KEY, nextWorkspaceId);
    setWorkspaceId(nextWorkspaceId);
    clearMessages();
  }

  async function addWorkspace(root) {
    const entry = await createWorkspace(root);
    await refreshWorkspaces();
    selectWorkspace(entry.id);
    return entry;
  }

  // 工具模式开关：开时把工具说明拼进面板（看得见的拼接，不是后端黑盒），
  // 关时还原成开之前的提示词。拼好的全文随请求原样发送，后端不再自动拼接。
  //
  // 抽成函数是因为它有两个入口：用户勾选框，以及**从会话恢复设置**。
  // 只在勾选框里做拼接的话，恢复那条路会漏掉拼接，面板显示的和实际发出去的就对不上。
  function applyToolsEnabled(next) {
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

  function handleToolsToggle(event) {
    const next = event.target.checked;
    applyToolsEnabled(next);
    // 存回这场对话 —— 切走再切回来时它跟着变回来。
    updateSessionSettings(sessionId, { toolsEnabled: next }).catch(() => {});
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
        workspace={workspaces.find((item) => item.id === workspaceId) || null}
        workspaces={workspaces}
        sessions={sessions}
        activeId={sessionId}
        collapsed={sidebarCollapsed}
        onToggle={toggleSidebar}
        onSelectWorkspace={selectWorkspace}
        onAddWorkspace={addWorkspace}
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
