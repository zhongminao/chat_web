import React, { useEffect, useRef, useState } from "react";

import {
  approveBashRequest,
  createWorkspace,
  deleteSession,
  deleteWorkspace,
  fetchProviders,
  fetchSessionItems,
  fetchSessions,
  fetchWorkspaces,
  interruptSession,
  rejectBashRequest,
  sendChat,
} from "./api";
import { newSessionId, readStoredSessionId, writeStoredSessionId } from "./session";
import { readStored, writeStored } from "./storage";
import BashApprovalPanel from "./components/BashApprovalPanel";
import Composer from "./components/Composer";
import MessageList from "./components/MessageList";
import PlanPanel from "./components/PlanPanel";
import SessionSidebar from "./components/SessionSidebar";
import SystemPanel from "./components/SystemPanel";

const SIDEBAR_KEY = "chat.sidebarCollapsed";
const WORKSPACE_KEY = "chat.workspaceId";
// 还没开始那场对话的工具开关草稿。它属于**界面**状态：会话在第一次发消息之前
// 不应该有文件（见 session_store.load_settings 那段注释），所以这个选择先存在本地，
// 等第一条消息发出去时由 /api/chat 的 tools_enabled 带进那一轮的记录里。
const TOOLS_DRAFT_KEY = "chat.toolsEnabledDraft";
const SANDBOX_DRAFT_KEY = "chat.sandboxModeDraft";

function createId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

// 服务端的 items → 展示列表。
//
// **id 必须跨轮询稳定**（用 sessionId + 下标），不能用 createId()。原因：它被当作
// React 的 key，而轮询每秒都会重新生成整个列表 —— 随机 key 会让 React 认定每一行
// 都是新元素，全部卸载重建，于是 <details> 的展开状态（那是 DOM 状态，不是 props）
// 每次轮询都被清掉：用户点开的工具输出会自己折叠回去。
//
// 下标是稳的：日志只追加，所以同一个下标永远指向同一条记录。唯一会变的是最后那个
// running → step（工具跑完），那时元素类型从 div 变成 details，本来就该重建。
//
// **kind 的映射是穷举的，未知 kind 不再静默**：以前兜底分支把所有不认识的东西
// 当成一条 assistant 消息画出来 —— 后端加了新 kind，界面上看不出来，只是内容怪。
// 现在落到 unknown，MessageList 会明说"未知条目"。配合契约里的 item_kind 取值域，
// "后端能发什么"和"前端能画什么"两边一样大（smoke 会逐一渲染来验）。
function toDisplayItems(items, sessionId) {
  return (items || []).map((item, index) => {
    const id = `${sessionId}:${index}`;
    switch (item.kind) {
      case "user":
        return { id, role: "user", content: item.content };
      case "step":
        return {
          id,
          role: "tool-step",
          tool: item.tool,
          result: item.result,
          ok: item.ok,
        };
      case "running":
        // 正在跑的那一个（声明了调用、结果还没落盘）。只在轮询过程中出现 ——
        // 见 session_store.load_items 的说明。
        return { id, role: "tool-running", tool: item.tool };
      case "bash-request":
        return {
          id,
          role: "bash-request",
          requestId: item.id,
          command: item.command,
          cwd: item.cwd,
          status: item.status,
          result: item.result,
        };
      case "plan":
        return { id, role: "plan", todos: Array.isArray(item.todos) ? item.todos : [] };
      case "assistant":
        return { id, role: "assistant", content: item.content };
      default:
        return { id, role: "unknown", kind: String(item.kind) };
    }
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
  // 点过停止、但那一轮还没真正收尾（协作式取消：要等到下一个步边界）。
  // 两个状态分开是因为它们的含义不同：isLoading = 服务端那一轮还在跑，
  // isStopping = 用户已经要求它停 —— 界面在两者之间显示"正在停止…"。
  const [isStopping, setIsStopping] = useState(false);
  const [providers, setProviders] = useState([]);
  const [provider, setProvider] = useState("");
  const [modelName, setModelName] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  // 服务端拼好的两份成品提示词（tools-off / tools-on）。面板里显示和发送的永远是
  // **具体文本**，前端不持有"片段 + 拼接规则"那套知识。
  const [promptPlain, setPromptPlain] = useState("");
  const [promptWithTools, setPromptWithTools] = useState("");
  const [isSystemPanelOpen, setIsSystemPanelOpen] = useState(false);
  const [toolsEnabled, setToolsEnabled] = useState(false);
  const [sandboxMode, setSandboxMode] = useState(() => readStored(SANDBOX_DRAFT_KEY) || "workspace-write");
  const [temperature, setTemperature] = useState(null);
  // 正在提交审批的那个 bash requestId。审批是一次性的：点下去立刻让面板消失、
  // 输入框回来，后台异步执行并轮询拿结果 —— 不等同步接口阻塞界面。
  const [submittingBashId, setSubmittingBashId] = useState(null);
  // 侧栏收起状态存 localStorage —— 这是个界面偏好，刷新后不该跳回去。
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => readStored(SIDEBAR_KEY) === "1"
  );
  // 从会话恢复设置要等默认提示词到齐才能拼系统提示词，见下面那个 effect。
  const [loadedSettings, setLoadedSettings] = useState(null);
  const [providersReady, setProvidersReady] = useState(false);  // 说过的对话，工具开关锁死（服务端也会拒绝改，这里只是让界面说实话）。
  const [toolsLocked, setToolsLocked] = useState(false);

  // ── 轮询进度 ──────────────────────────────────────────────────────────────
  // 记录是**边跑边落盘**的（每条工具结果一产生就写），所以服务端那边从头到尾都有
  // 完整的进度；问题只在于没人去读 —— /api/chat 是一个长请求，整轮跑完才返回。
  // 所以这里在轮次进行中定时重取会话，把已经落盘的步骤画出来。
  //
  // 不引入 SSE/WebSocket：现有那个 GET /api/sessions/{id} 就是进度接口，日志是权威，
  // 读它不需要任何新机制。等轮询不够顺滑了（比如要做 token 级流式）再上 SSE。
  const pollTimerRef = useRef(null);   // setInterval 的句柄，收尾时清掉
  const pendingUserRef = useRef(null); // 本地先画出来的那条 user，防它被轮询结果闪没
  // 正在提交审批的那个 requestId。轮询回调是闭包，拿 state 会拿到旧值，所以用 ref。
  const submittingBashRef = useRef(null);

  function stopProgressPolling() {
    if (pollTimerRef.current !== null) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }

  function startProgressPolling(targetSessionId) {
    stopProgressPolling();
    pollTimerRef.current = setInterval(async () => {
      const data = await fetchSessionItems(targetSessionId).catch(() => null);
      if (!data) {
        return;
      }
      setMessages(applyServerItems(data.items, targetSessionId));
      if (!data.running) {
        // 服务端说这一轮已经结束了（可能是另一个标签页在跑，或者刚好收尾），
        // 那就不用再轮询了；sendMessage 那边会做最后的收尾渲染。
        stopProgressPolling();
      }
    }, 1000);
  }

  // 服务端的 items → 展示列表。**保留本地那条乐观消息**：它在 setMessages 里先画出来
  // 是为了手感（不等一个往返），而服务端是在收到请求之后才写 turn+user 的，第一次
  // 轮询有可能比它更早。直接覆盖会让刚发出去的消息闪一下不见。
  function applyServerItems(items, targetSessionId) {
    let server = toDisplayItems(items, targetSessionId);
    // 正在提交审批的那条：服务端此刻还没写 bash-result，轮询会把它拉回 pending ——
    // 面板刚离开又冒回来（用户看到的"闪一下"）。保持本地"正在执行"状态。
    // **只在服务端还没写结果时**才保持：一旦服务端给出 executed/rejected，就必须
    // 采信它 —— 否则最后一次刷新会把"已完成"覆盖回"正在执行"，而轮询已经停了，
    // 那条就永远卡在"正在执行…"（实测踩到过）。
    const submitting = submittingBashRef.current;
    if (submitting) {
      server = server.map((m) =>
        m.role === "bash-request" && m.requestId === submitting && m.status === "pending"
          ? { ...m, status: "submitting" }
          : m
      );
    }
    const pending = pendingUserRef.current;
    if (!pending) {
      return server;
    }
    const arrived = server.some(
      (message) => message.role === "user" && message.content === pending.content
    );
    return arrived ? server : [...server, pending];
  }

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
        // 服务端给的是**拼好的两份成品**（prompts.yaml → resolve_system_prompt），
        // 前端不再自己拼 —— 以前它抄了三处 `工具说明 + "\n\n" + 基础`，改一次分隔符
        // 三处都不会跟着动。这里只做一件事：按工具开关**选**哪一份。
        const plain = data.system_prompt_plain || "";
        const withTools = data.system_prompt_with_tools || "";
        setPromptPlain(plain);
        setPromptWithTools(withTools);
        setSystemPrompt(plain);   // 初始工具开关是关的
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

  // 工作区登记表。默认工作区由服务端决定 —— 本地存的那个可能已经被删，
  // 而"失效了该用哪个"是**服务端的判断**，它把答案放在 resolved 里。
  function refreshWorkspaces() {
    const stored = readStored(WORKSPACE_KEY);
    return fetchWorkspaces(stored)
      .then((data) => {
        setWorkspaces(data.workspaces || []);
        // 采纳服务端解析出来的那个。以前这里自己算（"store 的不在列表里 → 用
        // default"）= 同一条回落规则两处实现；现在只负责"服务端说用哪个就用哪个"。
        if (data.resolved && data.resolved !== stored) {
          writeStored(WORKSPACE_KEY, data.resolved);
          setWorkspaceId(data.resolved);
        }
        return data;
      })
      .catch(() => null);
  }

  useEffect(() => {
    refreshWorkspaces();
  }, []);

  // 卸载时清掉轮询定时器（切走页面不该还在那儿打接口）。
  useEffect(() => stopProgressPolling, []);

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
        setMessages(toDisplayItems(data?.items, sessionId));
        setLoadedSettings(data?.settings || {});
        setToolsLocked(Boolean(data?.toolsLocked));
        // 切到一场**正在跑**的对话（刷新页面、或从侧栏点进来）也要看到进度 ——
        // 这一轮跑在服务端，跟浏览器在不在没关系（running 就是服务端告诉我们的）。
        if (data?.running) {
          startProgressPolling(sessionId);
        } else {
          stopProgressPolling();
        }
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
    const recordedSandbox = loadedSettings.sandboxMode;
    setSandboxMode(recordedSandbox || readStored(SANDBOX_DRAFT_KEY) || "workspace-write");
    // **提示词也按记录恢复**：它跟工具开关一样是"这个对话在用什么"，记在每一轮的
    // turn 记录里（load_settings 取最后一轮）。以前它只活在 React state 里 ——
    // 刷新页面就回到默认，而会话历史还在服务端，两边对不上。
    if (typeof loadedSettings.systemPrompt === "string") {
      setSystemPrompt(loadedSettings.systemPrompt);
    }
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
    pendingUserRef.current = userMessage;
    setInput("");
    setIsLoading(true);
    // 从这一刻起就开始画进度：服务端每条记录一产生就落盘，轮询就能看到。
    startProgressPolling(sessionId);

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
        sandbox_mode: sandboxMode,
        temperature,
      });

      // 回执只说"服务端做了什么决定"（state + toolsLocked），**不含数据**。
      // 发生过什么一律去读日志（GET /api/sessions/{id}）—— 它是唯一权威，而且
      // 轮询早就把过程画出来了。
      setToolsLocked(Boolean(data.toolsLocked));

      const fresh = await fetchSessionItems(sessionId).catch(() => null);
      if (fresh) {
        const items = applyServerItems(fresh.items, sessionId);
        // 被中止**不是错误**。中止可能落在两处（调模型之前 / 工具之前），日志里
        // 未必留下显眼的痕迹，所以补一条本地提示，别让界面莫名其妙地停住。
        setMessages(data.state === "interrupted"
          ? [...items, { id: `${sessionId}:stopped`, role: "assistant", content: "（已停止）" }]
          : items);
      } else {
        // 读不到日志：**明说读不到**，不要拿响应体顶上 —— 回执里没有结果，而且
        // 一份可能过期的副本比"看不见"更坏（屏幕上会和磁盘上的记录不一致）。
        // 刷新页面就能看到（日志是持久的）。
        setMessages((currentMessages) => [
          ...currentMessages,
          { id: `${sessionId}:unreadable`, role: "assistant",
            content: data.state === "interrupted"
              ? "（已停止，但暂时读不到会话记录 —— 刷新页面就能看到）"
              : "（这一轮跑完了，但暂时读不到会话记录 —— 刷新页面就能看到）" },
        ]);
      }
      refreshSessions();   // 标题/轮数变了，侧栏跟着更新
    } catch (error) {
      // 失败只进展示：服务端也不把这次提问写进历史，所以重试是干净的。
      setMessages((currentMessages) => [
        ...currentMessages,
        { id: createId(), role: "assistant", content: `请求失败：${error.message}` },
      ]);
    } finally {
      stopProgressPolling();
      pendingUserRef.current = null;
      setIsLoading(false);
      setIsStopping(false);
    }
  }

  // 停止正在跑的那一轮。**不在这里 setIsLoading(false)** —— 取消是协作式的，
  // 那一轮要等到下一个步边界才真的停，界面得先显示"正在停止…"；真正的收尾在
  // sendMessage 拿到 /api/chat 的响应（interrupted: true）时做。
  async function refreshOpenSession() {
    const fresh = await fetchSessionItems(sessionId).catch(() => null);
    if (fresh) {
      setMessages(applyServerItems(fresh.items, sessionId));
    }
  }

  // 点下「允许一次/拒绝」后**立即**让面板消失、输入框回来（乐观标记 submitting），
  // 不等同步审批接口返回 —— 否则用户会看到界面卡住直到模型恢复跑完。
  function markBashSubmitting(requestId) {
    submittingBashRef.current = requestId;
    setSubmittingBashId(requestId);
    setMessages((currentMessages) =>
      currentMessages.map((m) =>
        m.role === "bash-request" && m.requestId === requestId
          ? { ...m, status: "submitting" }
          : m
      )
    );
  }

  async function approveBash(requestId) {
    markBashSubmitting(requestId);
    // 服务端执行 bash + 恢复 loop 需要几秒，轮询能逐步把 bash-result 和最终回复画出来。
    startProgressPolling(sessionId);
    try {
      await approveBashRequest(sessionId, requestId);
      await refreshOpenSession();
      refreshSessions();
    } catch (error) {
      setMessages((currentMessages) => [
        ...currentMessages.map((m) =>
          m.role === "bash-request" && m.requestId === requestId
            ? { ...m, status: "pending" }
            : m
        ),
        { id: createId(), role: "assistant", content: `批准 bash 失败：${error.message}` },
      ]);
    } finally {
      submittingBashRef.current = null;
      setSubmittingBashId(null);
      stopProgressPolling();
    }
  }

  async function rejectBash(requestId) {
    markBashSubmitting(requestId);
    try {
      await rejectBashRequest(sessionId, requestId);
      await refreshOpenSession();
      refreshSessions();
    } catch (error) {
      setMessages((currentMessages) => [
        ...currentMessages.map((m) =>
          m.role === "bash-request" && m.requestId === requestId
            ? { ...m, status: "pending" }
            : m
        ),
        { id: createId(), role: "assistant", content: `拒绝 bash 失败：${error.message}` },
      ]);
    } finally {
      submittingBashRef.current = null;
      setSubmittingBashId(null);
    }
  }

  async function stopTurn() {
    if (!isLoading || isStopping) {
      return;
    }
    setIsStopping(true);
    try {
      await interruptSession(sessionId);
    } catch (error) {
      // 请求都没发出去，那就别卡在"正在停止…"上。
      setIsStopping(false);
      setMessages((currentMessages) => [
        ...currentMessages,
        { id: createId(), role: "assistant", content: `停止失败：${error.message}` },
      ]);
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

  // 工具模式开关：**只负责在服务端给的两份成品之间切换**，不再自己拼。
  //
  // 边界：如果用户改过提示词（面板内容既不是 plain 也不是 with_tools），切换开关
  // **不动他的文本**。以前的做法是把工具说明"插"进用户改过的文本前面 —— 那等于
  // 静默改写了用户写的东西，和这个项目自己的原则（面板写什么、模型就看到什么）矛盾。
  // 代价是：自定义提示词 + 开工具时，工具说明不会自动加进去；面板显示的就是实际
  // 发送的内容，要加就自己粘。
  //
  // 抽成函数是因为它有两个入口：用户勾选框，以及**从会话恢复设置**。
  function applyToolsEnabled(next) {
    setToolsEnabled(next);
    const target = next ? promptWithTools : promptPlain;
    setSystemPrompt((current) =>
      current === promptPlain || current === promptWithTools || current === ""
        ? target
        : current
    );
  }

  function handleToolsToggle(event) {
    const next = event.target.checked;
    applyToolsEnabled(next);
    // 只存本地草稿，**不写服务端**：写过话的会话由最后一轮的记录说了算（服务端也会
    // 拒绝中途改），没写过话的会话本来就不该在磁盘上有文件。
    writeStored(TOOLS_DRAFT_KEY, next ? "1" : "0");
  }

  function handleSandboxChange(event) {
    const next = event.target.value;
    setSandboxMode(next);
    writeStored(SANDBOX_DRAFT_KEY, next);
  }

  function restoreDefaultSystemPrompt() {
    setSystemPrompt(toolsEnabled ? promptWithTools : promptPlain);
  }

  const lastUserIndex = messages.reduce(
    (last, message, index) => (message.role === "user" ? index : last),
    -1
  );
  const latestPlan = messages
    .slice(lastUserIndex + 1)
    .reverse()
    .find((message) => message.role === "plan") || null;

  // 待审批的 bash 请求：有它就占据 composer 位置显示审批面板（DSH 式 composer-takeover）。
  const pendingBash = messages.find(
    (m) =>
      m.role === "bash-request" &&
      m.status === "pending" &&
      m.requestId !== submittingBashId
  );

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
          <label
            className="tool-toggle"
            title={
              isLoading
                ? "这一轮正在执行，等它结束后可以切换沙箱模式。"
                : "workspace-write 只允许文件工具读写当前工作区并禁止 bash；full-access 不限制文件工具且允许 bash，bash 命令会审计。"
            }
          >
            沙箱
            <select
              value={sandboxMode}
              onChange={handleSandboxChange}
              disabled={isLoading}
            >
              <option value="workspace-write">workspace-write</option>
              <option value="full-access">full-access</option>
            </select>
          </label>
        </header>

        {isSystemPanelOpen ? (
          <SystemPanel
            systemPrompt={systemPrompt}
            onSystemPromptChange={setSystemPrompt}
            // 「恢复默认」恢复到**当前工具模式**对应的那份服务端成品
            restoreValue={toolsEnabled ? promptWithTools : promptPlain}
            onRestoreDefault={restoreDefaultSystemPrompt}
          />
        ) : null}

        <MessageList messages={messages} isLoading={isLoading} />

        <PlanPanel todos={latestPlan?.todos} />

        {pendingBash ? (
          <BashApprovalPanel
            command={pendingBash.command}
            cwd={pendingBash.cwd}
            submitting={submittingBashId === pendingBash.requestId}
            onApprove={() => approveBash(pendingBash.requestId)}
            onReject={() => rejectBash(pendingBash.requestId)}
          />
        ) : (
          <Composer
            input={input}
            onInputChange={setInput}
            onSubmit={handleSubmit}
            onKeyDown={handleKeyDown}
            isLoading={isLoading}
            isStopping={isStopping}
            onStop={stopTurn}
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
        )}
      </section>
    </main>
  );
}
