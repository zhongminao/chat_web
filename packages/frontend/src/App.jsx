import React, { useEffect, useState } from "react";

import {
  createWorkspace,
  deleteSession,
  deleteWorkspace,
  fetchProviders,
  fetchSessionItems,
  fetchSessions,
  fetchWorkspaces,
  sendChat,
} from "./api";
import { newSessionId, readStoredSessionId, writeStoredSessionId } from "./session";
import { readStored, writeStored } from "./storage";
import Composer from "./components/Composer";
import MessageList from "./components/MessageList";
import SessionSidebar from "./components/SessionSidebar";
import SystemPanel from "./components/SystemPanel";

const SIDEBAR_KEY = "chat.sidebarCollapsed";
const WORKSPACE_KEY = "chat.workspaceId";
// 还没开始那场对话的工具开关草稿。它属于**界面**状态：会话在第一次发消息之前
// 不应该有文件（见 session_store.load_settings 那段注释），所以这个选择先存在本地，
// 等第一条消息发出去时由 /api/chat 的 tools_enabled 带进那一轮的记录里。
const TOOLS_DRAFT_KEY = "chat.toolsEnabledDraft";

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
  // 说过的对话，工具开关锁死（服务端也会拒绝改，这里只是让界面说实话）。
  const [toolsLocked, setToolsLocked] = useState(false);

  function toggleSidebar() {
    setSidebarCollapsed((collapsed) => {
      writeStored(SIDEBAR_KEY, collapsed ? "0" : "1");
      return !collapsed;
    });
  }

  // 侧栏数据。每轮对话结束后要重取一次 —— 标题和轮数都跟着变。
  // **不带 workspaceId**：侧栏按工作区分组显示全部，不再是"只看当前那个"。
  function refreshSessions() {
    return fetchSessions()
      .then((data) => {
        setSessions(data.sessions || []);
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
        // 默认用服务端指定的那个，**不是列表第一个** —— 列表顺序只是 providers.yaml
        // 的书写顺序，跟"默认用哪个"是两件事（以前混在一起，导致改服务端默认值对
        // 界面完全无效）。服务端没给、或给的供应商不在列表里，才回落到第一个。
        const fallback = list[0];
        const chosen =
          list.find((entry) => entry.provider === data.default_provider) || fallback;
        if (chosen) {
          setProvider(chosen.provider);
          const wanted = chosen.provider === data.default_provider ? data.default_model : null;
          const inList = (chosen.models || []).some((model) => model.id === wanted);
          setModelName((inList && wanted) || chosen.models?.[0]?.id || "");
        }
        setProvidersReady(true);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    refreshSessions();
  }, []);

  // 工作区登记表。默认工作区由服务端决定（客户端本地存的 id 可能已经被删）。
  function refreshWorkspaces() {
    return fetchWorkspaces()
      .then((data) => {
        const list = data.workspaces || [];
        setWorkspaces(list);
        // 本地存的那个已经不在登记表里了 -> 落到默认那个，否则新对话没有落点。
        const stored = readStored(WORKSPACE_KEY);
        if (!list.some((entry) => entry.id === stored) && data.default) {
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
        setToolsLocked(Boolean(data?.toolsLocked));
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
    // 有记录的会话（说过话）以记录为准；新会话没有记录，用本地草稿 —— 这样
    // "发第一条消息之前拨了开关、然后刷新页面"这个选择不会丢，也仍然不落盘。
    const recorded = loadedSettings.toolsEnabled;
    applyToolsEnabled(recorded === undefined ? readStored(TOOLS_DRAFT_KEY) === "1" : Boolean(recorded));
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

  // 从侧栏点一场对话：**连它所属的工作区一起切** —— 分组视图里能点到别的
  // 工作区的对话，切过去之后「新对话」才落在对的地方。
  function selectSession(session) {
    if (session.workspaceId && session.workspaceId !== workspaceId) {
      writeStored(WORKSPACE_KEY, session.workspaceId);
      setWorkspaceId(session.workspaceId);
    }
    if (session.id === sessionId) {
      return;
    }
    writeStoredSessionId(session.id);
    setSessionId(session.id);
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

  // 登记一个已存在的目录，或在某个目录下新建一个再登记（后者由选择器传 parent+name）。
  async function addWorkspace(payload) {
    const entry = await createWorkspace(payload);
    await refreshWorkspaces();
    selectWorkspace(entry.id);
    return entry;
  }

  // 删工作区。里面还有对话时调用方（侧栏的确认框）会带 withSessions —— 服务端默认
  // 拒绝删非空工作区，必须显式说要连对话一起删，那是不可撤销的。
  //
  // 删完必须重取会话列表：少了这一步，刚被删掉的对话还留在界面上，而且因为它们的
  // 工作区没了会掉进"未分组"继续显示 —— 看着像没删掉。
  async function removeWorkspace(targetWorkspaceId, { withSessions = false } = {}) {
    const data = await deleteWorkspace(targetWorkspaceId, { withSessions });
    setWorkspaces(data.workspaces || []);

    // 当前开着的这场对话就在被删的里面 -> 换一场新会话。不换的话界面还停在一场
    // 已经不存在的对话上，下一轮会往一个没了的日志文件里写。
    const open = sessions.find((session) => session.id === sessionId);
    if (withSessions && open?.workspaceId === targetWorkspaceId) {
      clearMessages();
    }

    // 删掉的正是当前工作区 -> 落到服务端给的默认那个（它保证至少还剩一个）。
    if (targetWorkspaceId === workspaceId && data.default) {
      selectWorkspace(data.default);
    }
    await refreshSessions();
    return data;
  }

  // 删一场对话。日志文件就是它的全部历史，没有回收站，所以侧栏那边一律先确认。
  async function removeSession(targetSessionId) {
    await deleteSession(targetSessionId);
    // 删的正是当前这场 -> 换一场新会话，别停在已经不存在的对话上。
    if (targetSessionId === sessionId) {
      clearMessages();
    }
    await refreshSessions();
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
    // 只存本地草稿，**不写服务端**：写过话的会话由最后一轮的记录说了算（服务端也会
    // 拒绝中途改），没写过话的会话本来就不该在磁盘上有文件。
    writeStored(TOOLS_DRAFT_KEY, next ? "1" : "0");
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
        onRemoveWorkspace={removeWorkspace}
        onRemoveSession={removeSession}
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
            title={
              toolsLocked
                ? "这个对话已经说过了，工具开关不再可改 —— 它决定系统提示词里有没有工具说明，中途改会让模型看到的历史缺一块。要换就开新对话。"
                : "勾选后模型可调用 read/write/edit/bash 工具，面板会自动拼入工具说明"
            }
          >
            <input
              type="checkbox"
              checked={toolsEnabled}
              onChange={handleToolsToggle}
              disabled={isLoading || toolsLocked}
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
