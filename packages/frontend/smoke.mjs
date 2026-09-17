/* UI 冒烟测试：把真实产物 app.js 放进 jsdom 里跑，确认它真能渲染。
 *
 * 存在的理由：这个环境里没有浏览器，前端改动此前只能靠人肉点。而 React 的失败方式
 * 很安静（白屏 / 一个空 div），光看接口返回发现不了。
 *
 * 四个场景，各自是一个新 JSDOM：
 *   1. 全新会话   —— 能渲染、会自己生成 sessionId
 *   2. 恢复历史   —— localStorage 里有 id 时，把服务端的历史拉回来并渲染出来
 *   3. 存储不可用 —— localStorage 抛异常时**仍然能渲染**（不能白屏）
 *   4. 侧栏收起   —— 收起态下只剩窄条，不能还去断言展开态才有的东西
 *
 * 断言分两层：先"能不能起来"（白屏 / 组件抛异常），再点几下验证交互真的接上了
 * （分组折叠、搜索过滤、目录选择器、工具开关写回）。样式管不了 —— jsdom 不算布局，
 * 高度对齐这类事只能靠人眼，别在这里假装测了。
 *
 *   node smoke.mjs
 */
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

/* ──────────────────────────────────────────────────────────────────────────
 * 契约校验：本文件里那些 **stub 响应体**必须和后端真实的形状一致。
 *
 * 为什么需要它：这套测试把 fetch 全 stub 掉，验的是"给定数据下渲染对不对"——
 * 所以 stub 的响应体是**手写的**，信的是前端的想象，不是后端的事实。后端把
 * items 的 kind 改个名、把 settings 的键挪个位，这里不会有任何断言变红。
 *
 * 现在形状契约是 packages/chat/backend-contract.json（由
 * `python packages/chat/check_api.py --update` 从真实后端采出，进 git）：
 *   - 后端改字段 → check_api.py 红（契约过期）→ 你 --update 更新契约
 *   - 契约更新之后 → **这个校验立刻发现 stub 还是旧形状** → 要么改前端、要么改 stub
 * 于是那条边界两侧都被同一份文件审着。
 * ────────────────────────────────────────────────────────────────────────── */
const CONTRACT_PATH = join(dirname(fileURLToPath(import.meta.url)), "..", "chat", "backend-contract.json");
const contractFile = JSON.parse(readFileSync(CONTRACT_PATH, "utf8"));
const contract = contractFile.endpoints;
/** 后端能产出的 item kind（load_items 声明的取值域）。前端那张映射表必须覆盖它。 */
const itemKinds = contractFile.enums?.item_kind ?? [];

/** 与 check_api.py 的 shape_of 同一套规则（那边是 Python，没法共用代码）。 */
function shapeOf(value) {
  if (value === null) return "null";
  if (typeof value === "boolean") return "bool";
  if (typeof value === "number") return Number.isInteger(value) ? "int" : "float";
  if (typeof value === "string") return "str";
  if (Array.isArray(value)) {
    if (value.length === 0) return { list: "empty" };
    if (value.every((item) => item && typeof item === "object" && !Array.isArray(item) && "kind" in item)) {
      const byKind = {};
      for (const item of value) {
        const merged = byKind[item.kind];
        byKind[item.kind] = merged === undefined ? shapeOf(item) : mergeShape(merged, shapeOf(item));
      }
      return { list_by_kind: sortKeys(byKind) };
    }
    let merged = shapeOf(value[0]);
    for (const item of value.slice(1)) merged = mergeShape(merged, shapeOf(item));
    return { list: merged };
  }
  if (typeof value === "object") {
    const out = {};
    for (const key of Object.keys(value).sort()) out[key] = shapeOf(value[key]);
    return out;
  }
  return typeof value;
}

function tagOf(shape) {
  if (shape && typeof shape === "object") {
    return "list" in shape || "list_by_kind" in shape ? "list" : "object";
  }
  return String(shape);
}

function sortKeys(object) {
  const out = {};
  for (const key of Object.keys(object).sort()) out[key] = object[key];
  return out;
}

function mergeShape(a, b) {
  if (JSON.stringify(a) === JSON.stringify(b)) return a;
  const isObj = (x) => x && typeof x === "object" && !Array.isArray(x);
  // 空列表不携带元素形状 → 和任何元素形状都合得来（否则"这次 steps 是空数组"会变成假差异）
  if (isObj(a) && a.list === "empty") return b;
  if (isObj(b) && b.list === "empty") return a;
  if (isObj(a) && isObj(b)) {
    if ("list" in a && "list" in b) return { list: mergeShape(a.list, b.list) };
    if ("list_by_kind" in a && "list_by_kind" in b) {
      const out = { ...a.list_by_kind };
      for (const [kind, shape] of Object.entries(b.list_by_kind)) {
        out[kind] = kind in out ? mergeShape(out[kind], shape) : shape;
      }
      return { list_by_kind: sortKeys(out) };
    }
    const out = { ...a };
    for (const [key, shape] of Object.entries(b)) {
      out[key] = key in out ? mergeShape(out[key], shape) : shape;
    }
    return sortKeys(out);
  }
  if (typeof a === "string" && typeof b === "string") {
    return [...new Set([...a.split("|"), ...b.split("|")])].sort().join("|");
  }
  return [tagOf(a), tagOf(b)].sort().join("|");
}

/* 两侧的同一条规则：**多出来的字段也算差异**。后端悄悄加一个字段时，前端还不知道它，
   这件事应该被看见，而不是让契约慢慢变成一份没人对得上的文档。 */
function diffShape(actual, expected, path = "$") {
  if (JSON.stringify(actual) === JSON.stringify(expected)) return [];
  const isObj = (x) => x && typeof x === "object" && !Array.isArray(x);
  // 空列表不携带形状信息，两个方向都算一致（见 mergeShape 里同一句的理由）
  for (const side of [actual, expected]) {
    if (isObj(side) && side.list === "empty") return [];
  }
  if (isObj(actual) && isObj(expected)) {
    const listMismatch = ("list" in actual) !== ("list" in expected)
      || ("list_by_kind" in actual) !== ("list_by_kind" in expected);
    if (listMismatch) return [`${path}: 列表形态变了（${tagOf(expected)} → ${tagOf(actual)}）`];
    const differences = [];
    for (const key of [...new Set([...Object.keys(actual), ...Object.keys(expected)])].sort()) {
      if (!(key in actual)) differences.push(`${path}.${key}: 契约里有、stub 里没有`);
      else if (!(key in expected)) differences.push(`${path}.${key}: stub 里多出来（后端没有它）`);
      else differences.push(...diffShape(actual[key], expected[key], `${path}.${key}`));
    }
    return differences;
  }
  return [`${path}: 契约 ${JSON.stringify(expected)} → stub ${JSON.stringify(actual)}`];
}

/** 把一份 stub 响应体对着契约里的某个接口校验。返回差异列表（空 = 一致）。 */
function checkAgainstContract(endpoint, payload) {
  const expected = contract[endpoint];
  if (!expected) return [`契约里没有 ${endpoint}（跑 check_api.py --update 生成）`];
  return diffShape(shapeOf(payload), expected, endpoint);
}

/* 契约自检：契约文件本身得在、几个关键接口得在里面。缺了说明生成那步没跑过。 */
const contractSelfCheck = [];
for (const endpoint of ["GET /api/providers", "GET /api/sessions", "GET /api/sessions/{id}",
                        "GET /api/workspaces", "POST /api/chat"]) {
  if (!contract[endpoint]) contractSelfCheck.push(`契约缺 ${endpoint}`);
}


const STATIC_DIR = new URL("../chat/src/chat/static/", import.meta.url);
const html = readFileSync(new URL("index.html", STATIC_DIR), "utf-8");
const bundle = readFileSync(new URL("app.js", STATIC_DIR), "utf-8");

// 故意把"列表第一个"和"默认那个"设成不同的：界面必须听 default_provider/default_model，
// 而不是 providers.yaml 的书写顺序（以前就是这么错的 —— 改服务端默认值对界面无效）。
const providers = {
  providers: [
    { provider: "gpt", display_name: "GPT (忘川_网关)",
      models: [{ id: "gpt-5.5", name: "GPT-5.5", temperature: 0.2 }] },
    { provider: "deepseek", display_name: "DeepSeek",
      models: [{ id: "deepseek-flash", name: "DeepSeek Flash", temperature: 0.2 }] },
  ],
  default_provider: "deepseek",
  default_model: "deepseek-flash",
  // 服务端给的是**拼好的两份成品**，不是模板碎片 —— 值抄自 prompts.yaml
  // （改动 prompts.yaml 之后这里要跟着更新；后端那边 check_api.py 会验真）。
  // 前端只按工具开关**选**哪一份，自己不拼。
  system_prompt_plain: "You are a helpful assistant. Keep context across turns and answer in the same language as the user when possible.",
  system_prompt_with_tools: "You are an agent that can take real actions through tools.\nTools available:\nread_file — read any UTF-8 text file (page large files with offset/limit);\nwrite_file — create a new file or fully overwrite one, parent directories are created automatically (use ONLY for new files or complete rewrites);\nedit_file — replace exactly one text block in an existing file (old_text must be copied verbatim from read_file output, never invented);\nrun_bash — execute a shell command (ls, grep, git, run programs);\nplan — create or update the current task checklist for longer multi-step work.\nRules: always read a file before editing or quoting it; never invent file contents; do not reuse or overwrite existing helper scripts (such as run_task.sh) for ad-hoc tests — create a uniquely named file instead. When the task is done, reply concisely in the user's language and summarize what you read, wrote, edited, or ran. Do not describe or speculate about sandbox or permission settings; report only what the tools actually returned.\n\nYou are a helpful assistant. Keep context across turns and answer in the same language as the user when possible.",
};

/* 服务端会返回的历史（渲染顺序：user / step / assistant 交替）。
 *
 * 最后那行是**符号保真**用的：路径通配（`src/chat/*.py`）和乘法里的星号一旦被
 * 当成强调定界符吃掉，界面上看不见、复制出来却少字符（README 里满地都是 `*.jsonl`
 * 这种路径）。逐字断言它们必须在 textContent 里原样出现。
 * `\*` 是转义：应当只留星号，反斜杠不显示。 */
const storedItems = [
  { kind: "user", content: "历史里的第一个提问：**用户原文不渲染**" },
  { kind: "step", tool: "run_bash", arguments: '{"command":"echo hi"}',
    result: "$ echo hi\nhi\n[exit code: 0]", ok: true },
  { kind: "assistant", content: "历史里的回答\n\n- Markdown 列表项\n\n`inline_code`\n\n| 项 | 事实 |\n|---|---|\n| 技术栈 | React 18 + esbuild |\n\n```python\nprint(\"hi\")\n```\n\n保真：packages/chat/src/chat/*.py 与 *.jsonl 与 2 * 3 与 4*5 与 \\*字面星号\\* 与 **粗体**" },
];

/* ── 响应体的**唯一构造点** ────────────────────────────────────────────────
 * fetch stub 和契约校验**都调这两个函数**。分开写就会出这种事：契约校验去验一个
 * 手写的常量，而 stub 实际返回的是另一份自己拼的对象 —— 于是校验通过、页面却坏了
 * （真踩过：给 /api/workspaces 加了 resolved，只加在常量上，stub 那份漏了，
 * 侧栏工作区名就变成了兜底文案）。 */

/** GET /api/workspaces。workspaceId 由 URL 传入 —— 服务端按它解析 resolved。 */
function workspacesBody(workspaces, url = "") {
  const asked = /[?&]workspaceId=([^&]*)/.exec(url)?.[1];
  const askedId = asked ? decodeURIComponent(asked) : null;
  const known = askedId && workspaces.some((entry) => entry.id === askedId);
  return {
    workspaces,
    default: workspaces[0]?.id,
    // 有效就原样返回，无效（或没问）就回落默认 —— 和 resolve_workspace 同一个语义
    resolved: known ? askedId : workspaces[0]?.id,
  };
}

/** GET /api/sessions（列表；单场那个接口在别处）。 */
function sessionsBody(workspaces, sessions) {
  return { workspace: workspaces[0], sessions };
}
const workspacePayload = {
  workspaces: [
    { id: "ws-bc8da407", name: "chat", root: "/home/zhong/mydisk/tools/chat" },
    { id: "ws-8c393341", name: "tmp", root: "/tmp" },
  ],
  default: "ws-bc8da407",
  // 服务端按传进来的 workspaceId 解析出的"该用哪个"（无效就回落默认）。
  // 客户端采纳它，不自己算回落 —— 那是服务端的判断。
  resolved: "ws-bc8da407",
};

const sessionList = {
  workspace: workspacePayload.workspaces[0],
  sessions: [
    // 第一场：别的断言（标题、历史回放）依赖它，别让它被删除测试消耗掉。
    // createdAt 是契约要求的字段（后端确实会给）—— 少了它契约校验会红。
    { id: "web-test-restore", title: "侧栏里的会话标题", turns: 3,
      createdAt: Date.now() - 3600000, lastActivity: Date.now(), workspaceId: "ws-bc8da407" },
    // 第二场：专门用来验证"删一场对话"能精确删掉它、且不连累别的。
    { id: "web-test-doomed", title: "注定被删的对话", turns: 2,
      createdAt: Date.now() - 7200000, lastActivity: Date.now() - 60000,
      workspaceId: "ws-bc8da407" },
  ],
};

/* 目录选择器调的接口。entries 必须给真数组 —— 少了这个字段，组件里
 * listing.entries.length 会当场抛异常，整个界面白屏（这里踩过）。 */
const browsePayload = {
  path: "/home/zhong",
  parent: "/home",
  entries: [
    { name: "proj", path: "/home/zhong/proj" },
    { name: "notes", path: "/home/zhong/notes" },
  ],
};

let failures = 0;

/* ── 契约校验：stub 的响应体必须和后端真实的形状一致 ──────────────────────
 * 这是这套测试**唯一一条跨过前后端边界的断言**。它跑在最前面，因为后面所有渲染
 * 断言都建立在"stub 的响应体是后端真会给的形状"这个前提上。 */
console.log("\n[0. 契约：本文件的 stub 响应体 vs 后端形状契约]");
{
  const report = [...contractSelfCheck];
  // /api/providers 的 stub（每个场景都用它）
  report.push(...checkAgainstContract("GET /api/providers", providers));
  // /api/sessions 的 stub（侧栏）—— 调**实际构造它的那个函数**，不是抄一份常量
  report.push(...checkAgainstContract("GET /api/sessions",
    sessionsBody(workspacePayload.workspaces, sessionList.sessions)));
  // /api/workspaces 的 stub —— 同上，两种解析情形都过一遍
  report.push(...checkAgainstContract("GET /api/workspaces",
    workspacesBody(workspacePayload.workspaces, "")));
  report.push(...checkAgainstContract("GET /api/workspaces",
    workspacesBody(workspacePayload.workspaces, "?workspaceId=ws-8c393341")));
  report.push(...checkAgainstContract("GET /api/workspaces",
    workspacesBody(workspacePayload.workspaces, "?workspaceId=ws-gone")));
  // /api/sessions/{id} 的 stub：
  //   storedItems 是"恢复历史"那场的（user / step / assistant 三种，没有 running）
  //   进度轮询那场把它扩到四种都出现
  const richItems = [
    { kind: "user", content: "x" },
    { kind: "step", tool: "t", arguments: "{}", result: "r", ok: true },
    { kind: "running", tool: "t", arguments: "{}" },
    { kind: "bash-request", id: "bashreq-smoke", command: "printf hi", cwd: "/tmp/ws", timeout: 60, status: "pending", result: "" },
    { kind: "plan", todos: [{ content: "检查现状", status: "completed" }, { content: "继续执行", status: "in_progress" }] },
    { kind: "assistant", content: "x" },
  ];
  report.push(...checkAgainstContract("GET /api/sessions/{id}", {
    id: "x", workspaceId: "ws", settings: { toolsEnabled: true, systemPrompt: "p" },
    toolsLocked: true, running: false, items: richItems,
  }));
  // POST /api/chat 的 stub：**状态回执**（没有 reply / steps 了 —— 发生过什么去读
  // GET /api/sessions/{id}）。成功和中止两种 state 都要对得上契约。
  report.push(...checkAgainstContract("POST /api/chat", { state: "ok", toolsLocked: true }));
  report.push(...checkAgainstContract("POST /api/chat", { state: "interrupted", toolsLocked: true }));
  report.push(...checkAgainstContract("POST /api/sessions/{id}/bash-requests/{requestId}/approve", { status: "executed", content: "ok", reply: "执行成功" }));
  report.push(...checkAgainstContract("POST /api/sessions/{id}/bash-requests/{requestId}/reject", { status: "rejected", content: "no", reply: "好的" }));
  // POST /api/sessions/{id}/interrupt 的 stub
  report.push(...checkAgainstContract("POST /api/sessions/{id}/interrupt", { interrupted: true }));

  if (report.length === 0) {
    console.log("  ✅ 7 个接口的 stub 形状都和契约一致");
  } else {
    for (const line of report.slice(0, 8)) console.log(`  ❌ ${line}`);
    if (report.length > 8) console.log(`  ❌ …另有 ${report.length - 8} 处`);
    console.log("  → stub 过期了。契约是 check_api.py --update 从真实后端采的：");
    console.log("     先跑 `python packages/chat/check_api.py`，红了就读它的提示，");
    console.log("     再按后端实际返回的形状改这里的 stub（或改前端去适配）。");
    failures += 1;
  }
}

async function scenario(name, { withUrl = true, seedSession = null, sessionItems = null, collapsed = false, expectTools = false, expectLocked = false, progressSequence = null, expectApproval = false } = {}) {
  const pageErrors = [];
  const fetchCalls = [];
  const patched = [];
  const clipboardWrites = [];
  let pollFrames = 0;   // 进度轮询场景：每次 GET /api/sessions/{id} 返回下一帧
  // 每个场景一份可变的登记表：DELETE 之后要真的少一项，否则"删完列表还在"这种
  // bug 测不出来（stub 原样返回旧列表就等于假装删成功了）。
  let workspaces = workspacePayload.workspaces.map((entry) => ({ ...entry }));
  // 会话列表也要可变：stub 原样返回旧列表就等于假装删成功了。
  let sessions = sessionList.sessions.map((entry) => ({ ...entry }));
  const deletedSessions = [];
  const deletedWorkspaces = [];

  const dom = new JSDOM(html, {
    // url 不能省：jsdom 默认 origin 是 about:blank（不透明 origin），访问 localStorage
    // 会抛 SecurityError —— 而应用启动时要读 chat.sessionId。真实浏览器有 http origin。
    // 场景 3 故意不给 url，用来验证"存储不可用也不白屏"。
    ...(withUrl ? { url: "http://localhost:8200/" } : {}),
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const { window } = dom;

  window.addEventListener("error", (event) =>
    pageErrors.push(String(event.error || event.message)));
  window.console.error = (...args) => pageErrors.push(args.map(String).join(" "));
  Object.defineProperty(window.navigator, "clipboard", {
    configurable: true,
    value: { writeText: async (value) => { clipboardWrites.push(value); } },
  });

  if (seedSession) {
    window.localStorage.setItem("chat.sessionId", seedSession);
  }
  if (collapsed) {
    window.localStorage.setItem("chat.sidebarCollapsed", "1");
  }

  window.fetch = (url, options) => {
    const target = String(url);
    fetchCalls.push(target);
    if (options?.method === "PATCH") {
      patched.push({ url: target, body: options.body });
    }
    // 三个接口的路径要分清：/api/workspaces、/api/sessions（可带 ?workspaceId=）、
    // /api/sessions/<id>。用 includes 一刀切会把它们搞混。
    const method = options?.method || "GET";

    // 进度轮询场景：/api/chat 拖到 5 秒后才返回（模拟"这一轮还在跑"，前端于是停在
    // isLoading=true 并持续轮询），而 GET /api/sessions/{id} 每次返回下一帧
    // （模拟服务端边跑边落盘）。最后一帧会被反复返回。
    //
    // 为什么不是"永不返回"：那样前端的收尾分支永远不执行，轮询定时器就不清，
    // jsdom 的事件循环一直活着 —— 冒烟会挂到超时（踩过）。
    if (progressSequence) {
      if (target.includes("/api/chat")) {
        return new Promise((resolve) => setTimeout(
          () => resolve({ ok: true, json: () => Promise.resolve({ state: "ok", toolsLocked: true }) }),
          5000,
        ));
      }
      if (method === "GET" && target.includes("/api/sessions/")) {
        const frame = progressSequence[Math.min(pollFrames++, progressSequence.length - 1)];
        return Promise.resolve({ ok: true, json: () => Promise.resolve(frame) });
      }
    }

    // 删一场对话
    if (method === "DELETE" && target.includes("/api/sessions/")) {
      const id = decodeURIComponent(target.split("/api/sessions/")[1].split("?")[0]);
      deletedSessions.push(id);
      if (seedSession === id) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      }
      sessions = sessions.filter((entry) => entry.id !== id);
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ deleted: id }) });
    }

    // 删工作区。服务端语义：非空且没带 withSessions 就 409。
    if (method === "DELETE" && target.includes("/api/workspaces")) {
      const id = decodeURIComponent(target.split("/api/workspaces/")[1].split("?")[0]);
      const withSessions = target.includes("withSessions=true");
      const held = sessions.filter((entry) => entry.workspaceId === id);
      deletedWorkspaces.push({ id, withSessions, held: held.length });
      if (held.length > 0 && !withSessions) {
        return Promise.resolve({
          ok: false,
          status: 409,
          json: () => Promise.resolve({ detail: `还有 ${held.length} 场对话在这个工作区里` }),
        });
      }
      sessions = sessions.filter((entry) => entry.workspaceId !== id);
      workspaces = workspaces.filter((entry) => entry.id !== id);
      // 服务端删空了会把自己复活（保证至少有一个工作区可回落），stub 照做，
      // 否则会测出"一个工作区都不剩"这种真实服务端不会进入的状态。
      if (workspaces.length === 0) {
        workspaces = [{ id: "ws-default", name: "chat", root: "/home/zhong/mydisk/tools/chat" }];
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          workspaces, default: workspaces[0]?.id, deletedSessions: held.length,
        }),
      });
    }

    let body = providers;
    if (target.includes("/api/browse")) {
      body = browsePayload;
    } else if (target.includes("/api/workspaces")) {
      // 这些响应体的构造**必须走下面那几个函数**（workspacesBody / sessionsBody），
      // 契约校验也调它们 —— 否则就成了"校验我写的常量、而不是实际返回的东西"。
      body = workspacesBody(workspaces, target);
    } else if (target.includes("/api/sessions/")) {
      body = sessionItems ?? {};
    } else if (target.includes("/api/sessions")) {
      body = sessionsBody(workspaces, sessions);
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
  };

  try {
    window.eval(bundle);
  } catch (error) {
    console.log(`\n[${name}]`);
    console.log(`  ❌ bundle 执行抛出异常: ${error.message}`);
    failures += 1;
    return;
  }

  // React 18 的 render 是异步的，等一轮微任务 + 一个宏任务
  await new Promise((resolve) => setTimeout(resolve, 50));

  const root = window.document.getElementById("root");
  const html2 = root.innerHTML || "";
  const text = root.textContent || "";

  console.log(`\n[${name}]`);
  const check = (label, condition, detail = "") => {
    console.log(`  ${condition ? "✅" : "❌"} ${label}${detail ? "  " + detail : ""}`);
    if (!condition) failures += 1;
  };

  check("#root 非空（没白屏）", html2.length > 0, `${html2.length} 字符`);
  check("请求了供应商目录", fetchCalls.some((u) => u.includes("/api/providers")));
  if (expectApproval) {
    // 待审批：输入端被审批面板**接管**（所以没有输入框/发送按钮/模型 chip），
    // 而对话流里不该再出现"Bash 请求"卡片 —— 审批不干扰对话本身。
    check("输入端显示审批面板", !!window.document.querySelector(".bash-approval-card"));
    check("审批面板带「等待审批」提示", text.includes("等待审批"));
    check("审批面板显示要执行的命令", text.includes("printf hi"));
    check("审批期间发送按钮让位给面板",
          !window.document.querySelector("button[type=submit]"));
    check("对话流里没有 Bash 请求卡片", !text.includes("Bash 请求"));
  } else {
    check("输入框存在", !!window.document.querySelector("textarea, input[type=text]"));
    // 覆盖 providers -> state -> 渲染 这条链：接口回来了要真的显示到 chip 上
    // 断言的是"默认那个"，不是"列表第一个"：fixture 里列表第一个是 GPT-5.5，
    // 默认给的是 DeepSeek Flash —— 只有真听了 default_model 才会显示后者。
    check("模型 chip 显示的是 default_model（不是列表第一个）",
          text.includes("DeepSeek Flash") && !text.includes("GPT-5.5"),
          `实得 ${JSON.stringify(text.slice(0, 60))}`);
    check("发送按钮在", !!window.document.querySelector("button[type=submit]"));
  }
  // 侧栏内容只在展开时才有 —— 收起场景里断言这些等于自相矛盾，所以按状态分开。
  // 刻意取元素而不是全文 includes："chat" 这种短串用 includes 判可能撞到别处，等于没测。
  if (!collapsed) {
    const workspaceEl = window.document.querySelector(".sidebar-workspace-name");
    const sessionTitleEl = window.document.querySelector(".session-item-title");
    check("侧栏工作区名 = 路径末端目录",
          workspaceEl?.textContent === "chat", `实得 ${JSON.stringify(workspaceEl?.textContent)}`);
    check("侧栏会话标题 = 接口返回的标题",
          sessionTitleEl?.textContent === "侧栏里的会话标题",
          `实得 ${JSON.stringify(sessionTitleEl?.textContent)}`);
    check("新对话按钮在", text.includes("新对话"));
  }
  check("收起/展开按钮在", !!window.document.querySelector(".sidebar-toggle"));
  // 工具开关跟着**对话**走：接口说这场对话开了工具，勾选框就该是勾上的。
  const toolsBox = window.document.querySelector(".tool-toggle input[type=checkbox]");
  check("工具模式勾选框状态与接口一致",
        toolsBox?.checked === expectTools, `实得 ${toolsBox?.checked}`);
  // 说过的对话，开关锁死（服务端也会拒绝改，这里测的是界面有没有说实话）
  check("已说过话的对话，工具开关置灰",
        toolsBox?.disabled === expectLocked, `disabled=${toolsBox?.disabled}`);

  // 会话行的时间要带月日 —— 原先当天只显示时刻，跨天只有一个 MM-DD。
  const metaEl = window.document.querySelector(".session-item-meta");
  if (!collapsed) {
    check("会话行时间带月日",
          /\d{2}-\d{2} \d{2}:\d{2}/.test(metaEl?.textContent || ""),
          `实得 ${JSON.stringify(metaEl?.textContent)}`);

    // 区块头：标签 + 搜索 + 右侧动作
    check("区块头有「工作区」标签",
          window.document.querySelector(".section-label")?.textContent === "工作区");
    // 分组视图：组头要显示工作区名，会话缩进挂在它下面
    check("按工作区分组：组头显示工作区名",
          window.document.querySelector(".group-name")?.textContent === "chat",
          `实得 ${JSON.stringify(window.document.querySelector(".group-name")?.textContent)}`);
    const searchInput = window.document.querySelector(".section-search-input");
    check("区块头有搜索输入框", !!searchInput);

    // 分组折叠：点组头的折叠按钮，它下面的会话行应当消失；再点回来。
    const groupToggle = window.document.querySelector(".group-main");
    const rowsBefore = window.document.querySelectorAll(".session-item").length;
    groupToggle?.click();
    await new Promise((resolve) => setTimeout(resolve, 20));
    const rowsCollapsed = window.document.querySelectorAll(".session-item").length;
    groupToggle?.click();
    await new Promise((resolve) => setTimeout(resolve, 20));
    const rowsBack = window.document.querySelectorAll(".session-item").length;
    check("分组可以折叠",
          rowsBefore > 0 && rowsCollapsed === 0 && rowsBack === rowsBefore,
          `${rowsBefore} -> ${rowsCollapsed} -> ${rowsBack}`);

    // 添加工作区：点 ＋ 应弹出目录选择器。
    const addButton = [...window.document.querySelectorAll(".section-icon-button")]
      .find((el) => el.getAttribute("aria-label") === "添加工作区");
    addButton?.click();
    await new Promise((resolve) => setTimeout(resolve, 30));
    const modal = window.document.querySelector(".modal");
    check("点 ＋ 弹出目录选择器", !!modal);
    // 光有壳不算数：接口给的子目录要真的列出来，否则选择器是个空盒子。
    const rowNames = [...(modal?.querySelectorAll(".picker-row-name") || [])]
      .map((el) => el.textContent.trim());
    check("选择器列出了接口返回的子目录",
          rowNames.includes("proj") && rowNames.includes("notes"),
          `实得 ${JSON.stringify(rowNames)}`);

    // 用户要求：左下角「新建工作区」，右下角取消/确认，且两者同处一行。
    // jsdom 没有布局，"一个高一个低"测不出来，但**结构**能测：同容器 + 先后顺序。
    const actions = modal?.querySelector(".modal-actions");
    const left = modal?.querySelector(".modal-actions-left");
    const right = modal?.querySelector(".modal-actions-right");
    const buttonIn = (scope, label) =>
      [...(scope?.querySelectorAll("button") || [])]
        .some((el) => el.textContent.trim() === label);
    check("左下角有「新建工作区」", buttonIn(left, "新建工作区"),
          `左下角实得 ${JSON.stringify([...(left?.querySelectorAll("button") || [])].map((e) => e.textContent.trim()))}`);
    check("右下角有取消与确认",
          buttonIn(right, "取消") && buttonIn(right, "选这个目录"));
    check("两组动作在同一个动作栏里（不是上下两行）",
          !!actions && !!left && !!right && left.parentElement === actions
          && right.parentElement === actions
          && (left.compareDocumentPosition(right) & window.Node.DOCUMENT_POSITION_FOLLOWING) !== 0);

    // 新建分支：点一下要出输入框，不是个死按钮。
    [...(left?.querySelectorAll("button") || [])]
      .find((el) => el.textContent.trim() === "新建工作区")?.click();
    await new Promise((resolve) => setTimeout(resolve, 20));
    check("点「新建工作区」出目录名输入框",
          !!modal?.querySelector(".picker-create input"),
          `实得 ${JSON.stringify(modal?.querySelector(".picker-create")?.innerHTML || "")}`);

    window.document.querySelector(".modal-backdrop")?.click();
    await new Promise((resolve) => setTimeout(resolve, 20));

    // 搜索得**真的过滤**，只断言输入框存在证明不了。
    // 直接赋 value 不会触发 React 的 onChange —— 它自己有个 value tracker，
    // 看到值没变就跳过。必须走原生 setter 再派发 input 事件。
    if (searchInput) {
      const nativeSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, "value").set;
      const type = (text) => {
        nativeSetter.call(searchInput, text);
        searchInput.dispatchEvent(new window.Event("input", { bubbles: true }));
      };
      type("绝对匹配不上的词");
      await new Promise((resolve) => setTimeout(resolve, 20));
      check("搜不到时列表为空",
            !(root.textContent || "").includes("侧栏里的会话标题")
            && (root.textContent || "").includes("没有匹配的对话"));
      type("侧栏里");
      await new Promise((resolve) => setTimeout(resolve, 20));
      check("搜得到时列表恢复", (root.textContent || "").includes("侧栏里的会话标题"));
      type("");
    }

    // ---- 删除工作区 / 删除对话 ----
    // 前两条补一次真实事故：删除入口曾经**永远看不见** —— .row-action 自己带着
    // display:none，而唯一负责显示它的选择器指向已经被删掉的旧菜单
    // .workspace-option。结果容器被 hover 出来了，里面的按钮还是不显示。
    //
    // jsdom 不算外部样式表的布局，测不出"能不能看见"，所以退一步查样式表**文本**：
    // 保护的是"按钮被一条无条件 display:none 按死"这个具体错误。
    //
    // 样式表拆成 6 张之后这里改成**从 index.html 里读链接列表**，而不是写死路径：
    // 那样加/删/重命名一张表时断言跟着走，不会悄悄测一个已经不存在的文件。
    const appCss = [...html.matchAll(/<link[^>]+href="\/static\/([^"]+\.css)"/g)]
      .map((m) => m[1])
      .map((href) => readFileSync(new URL(href, STATIC_DIR), "utf-8"))
      .join("\n");
    check("index.html 里的样式表都读得到（含拆分后的 6 张）",
          appCss.length > 2000 && appCss.includes(".message-action"),
          `实得 ${appCss.length} 字符`);
    // 注释要先剥掉：断言的是"没有这条规则"，而注释里正好会提到那个旧选择器。
    const cssRules = appCss.replace(/\/\*[\s\S]*?\*\//g, "");
    const rowActionBlock = cssRules.match(/\.row-action\s*\{([^}]*)\}/);
    check("行内操作按钮不再自带 display:none（曾把删除入口按死）",
          !!rowActionBlock && !/display:\s*none/.test(rowActionBlock[1]),
          `实得 ${JSON.stringify(rowActionBlock?.[1]?.trim().slice(0, 60))}`);
    check("没有指向已删菜单 .workspace-option 的显示规则",
          !cssRules.includes(".workspace-option"));

    check("每个工作区行都有删除按钮",
          window.document.querySelectorAll('button[aria-label^="删除工作区"]').length === 2,
          `实得 ${JSON.stringify([...window.document.querySelectorAll('button[aria-label^="删除工作区"]')].map((el) => el.getAttribute("aria-label")))}`);
    check("每场对话都有删除按钮",
          window.document.querySelectorAll('button[aria-label^="删除对话"]').length === 2,
          `实得 ${JSON.stringify([...window.document.querySelectorAll('button[aria-label^="删除对话"]')].map((el) => el.getAttribute("aria-label")))}`);

    const modalText = () => window.document.querySelector(".modal")?.textContent || "";
    const clickInModal = (label) =>
      [...(window.document.querySelector(".modal")?.querySelectorAll("button") || [])]
        .find((el) => el.textContent.trim() === label)?.click();
    const trashWorkspace = (name) =>
      [...window.document.querySelectorAll('button[aria-label^="删除工作区"]')]
        .find((el) => el.getAttribute("aria-label") === `删除工作区 ${name}`);
    const trashSession = (title) =>
      [...window.document.querySelectorAll('button[aria-label^="删除对话"]')]
        .find((el) => el.getAttribute("aria-label") === `删除对话 ${title}`);

    // 1) 有对话的工作区：先问，问的是"会连带删掉几场"。
    trashWorkspace("chat")?.click();
    await new Promise((resolve) => setTimeout(resolve, 30));
    check("删有对话的工作区先弹确认框", !!window.document.querySelector(".modal"));
    check("确认框弹出来时不发请求",
          deletedWorkspaces.length === 0, `实得 ${JSON.stringify(deletedWorkspaces)}`);
    check("确认框说清连带删几场 + 目录不动",
          modalText().includes("2 场对话") && modalText().includes("目录不会被删"),
          `实得 ${JSON.stringify(modalText().slice(0, 80))}`);
    clickInModal("取消");
    await new Promise((resolve) => setTimeout(resolve, 20));
    check("取消后不删工作区且关掉弹窗",
          deletedWorkspaces.length === 0 && !window.document.querySelector(".modal"));

    // 2) 删除对话：精确删掉点的那一场，另一场必须还在。
    trashSession("注定被删的对话")?.click();
    await new Promise((resolve) => setTimeout(resolve, 30));
    check("点删除对话先弹确认框", !!window.document.querySelector(".modal"));
    check("确认前不发删除请求", deletedSessions.length === 0, `实得 ${JSON.stringify(deletedSessions)}`);
    check("确认框说清删的是哪场、几轮、找不回来",
          modalText().includes("注定被删的对话") && modalText().includes("2 轮")
          && modalText().includes("找不回来"),
          `实得 ${JSON.stringify(modalText().slice(0, 80))}`);
    clickInModal("取消");
    await new Promise((resolve) => setTimeout(resolve, 20));
    check("取消后不删对话", deletedSessions.length === 0);

    trashSession("注定被删的对话")?.click();
    await new Promise((resolve) => setTimeout(resolve, 20));
    window.document.querySelector(".modal .danger-button")?.click();
    await new Promise((resolve) => setTimeout(resolve, 40));
    check("确认后删掉的是点的那一场",
          deletedSessions.length === 1 && deletedSessions[0] === "web-test-doomed",
          `实得 ${JSON.stringify(deletedSessions)}`);
    const titlesNow = [...window.document.querySelectorAll(".session-item-title")]
      .map((el) => el.textContent);
    check("被删的那场消失、另一场还在",
          !titlesNow.includes("注定被删的对话") && titlesNow.includes("侧栏里的会话标题"),
          `实得 ${JSON.stringify(titlesNow)}`);

    // 3) 空工作区：没有不可撤销的后果，直接删，不弹框。
    trashWorkspace("tmp")?.click();
    await new Promise((resolve) => setTimeout(resolve, 30));
    check("空工作区直接删，不弹确认框",
          deletedWorkspaces.length === 1 && deletedWorkspaces[0].id === "ws-8c393341"
          && deletedWorkspaces[0].withSessions === false && !window.document.querySelector(".modal"),
          `实得 ${JSON.stringify(deletedWorkspaces)}`);
    check("删掉的工作区从侧栏消失",
          ![...window.document.querySelectorAll(".group-name")].some((el) => el.textContent === "tmp"));

    // 4) 确认之后，工作区要带着 withSessions=true 才真删（服务端默认拒绝删非空工作区）。
    trashWorkspace("chat")?.click();
    await new Promise((resolve) => setTimeout(resolve, 30));
    check("再点一次仍先弹确认框（这次剩 1 场）",
          modalText().includes("1 场对话"), `实得 ${JSON.stringify(modalText().slice(0, 80))}`);
    window.document.querySelector(".modal .danger-button")?.click();
    await new Promise((resolve) => setTimeout(resolve, 40));
    const lastDelete = deletedWorkspaces[deletedWorkspaces.length - 1];
    check("确认后带 withSessions=true 删工作区与其中的对话",
          lastDelete?.id === "ws-bc8da407" && lastDelete?.withSessions === true,
          `实得 ${JSON.stringify(deletedWorkspaces)}`);
    check("删完关掉确认框且对话列表清空",
          !window.document.querySelector(".modal")
          && !(root.textContent || "").includes("侧栏里的会话标题"));
  }

  // 点一下开关：**只改本地草稿，不写服务端**。
  //
  // 以前这里 PATCH /api/sessions/{id} 把开关存回会话，代价是"还没发消息就拨开关"
  // 会在磁盘上造出一个只有 settings 记录、没有 header 的会话文件（侧栏里那个
  // 永远停在"未分组"的 (空对话)）。现在这条 PATCH 整个删了 —— 所以断言的**反面**
  // 才是重点：点开关不能产生任何网络写请求。
  const patchedBefore = patched.length;
  toolsBox?.click();
  await new Promise((resolve) => setTimeout(resolve, 20));
  // 读草稿要包 try：场景 3 故意不给 url（不透明 origin），localStorage 会抛
  // SecurityError —— 应用自己把它 catch 了，测试代码也得照做，否则测试先炸。
  const draft = () => {
    try {
      return window.localStorage.getItem("chat.toolsEnabledDraft");
    } catch (error) {
      return null;
    }
  };
  // 存储可不可用：场景 3 的 withUrl=false 就是在测"存储挂了也不能白屏"
  const storageWorks = withUrl;
  check("点开关不写服务端（不再是 PATCH）",
        patched.length === patchedBefore,
        `却发了 ${JSON.stringify(patched.slice(patchedBefore).map((c) => c.url))}`);
  if (expectLocked) {
    // 说过话的对话：勾选框置灰，点了什么都不该发生 —— 不写服务端，也不留草稿
    check("锁了的对话：开关置灰，点了不产生任何写入",
          toolsBox?.disabled === true && patched.length === patchedBefore
          && (!storageWorks || draft() === null),
          `disabled=${toolsBox?.disabled} 草稿=${JSON.stringify(draft())}`);
  } else if (storageWorks) {
    check("开关状态存进了本地草稿（不落盘）",
          draft() === (expectTools ? "0" : "1"), `实得 ${JSON.stringify(draft())}`);
  }

  // ── 轮询不能把已经展开的工具输出折叠回去 ──────────────────────────────────
  //
  // 守的是一个真实踩过的 bug：toDisplayItems 曾经每次轮询都用 createId() 生成随机 id，
  // 而它被当作 React 的 key —— 于是每次轮询 React 都认为所有行都是新元素、全部卸载
  // 重建，<details> 的展开状态（DOM 状态，不是 props）随之被清掉：用户点开的工具输出
  // 每隔一秒自己折回去一次。
  //
  // 修法是让 id 跨轮询稳定（`${sessionId}:${下标}`）。所以这里断言两件事：
  // 第二次轮询之后**还是同一个 DOM 节点**，且它仍然是打开的。
  if (progressSequence) {
    const box = window.document.querySelector("textarea");
    const form = window.document.querySelector("form");
    if (box && form) {
      // React 受控组件：必须走原生 setter + input 事件。直接 box.value = "..." 不会
      // 更新 React 的 state，于是 canSend 仍是 false，提交什么都不会发生。
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLTextAreaElement.prototype, "value").set;
      setter.call(box, "跑个命令");
      box.dispatchEvent(new window.Event("input", { bubbles: true }));
      await new Promise((resolve) => setTimeout(resolve, 30));
      form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
      await new Promise((resolve) => setTimeout(resolve, 1200));   // 等一次轮询

      const opened = window.document.querySelector("details.tool-step");
      check("轮询过程中画出了已完成的工具步骤", !!opened);
      if (opened) {
        opened.open = true;                                        // 模拟用户点开
        await new Promise((resolve) => setTimeout(resolve, 1200));  // 再等一次轮询
        const again = window.document.querySelector("details.tool-step");
        check("第二次轮询后展开状态还在（key 稳定，节点没被重建）",
              again === opened && again.open === true,
              `同一节点=${again === opened} open=${again?.open}`);
      }
    } else {
      check("进度场景：找到输入框与表单", false);
    }
  }

  if (pageErrors.length) {
    console.log("  --- 页面报错 ---");
    for (const line of pageErrors.slice(0, 3)) {
      console.log("    " + line.split("\n")[0].slice(0, 160));
    }
  }
  return { text, html: html2, fetchCalls, document: window.document, clipboardWrites };
}

// 场景 1：全新会话
const fresh = await scenario("1. 全新会话");
console.log(`  ${/web-\d+/.test(fresh.fetchCalls.join(" ")) ? "✅" : "❌"} 自己生成了 sessionId 并去取历史`);
if (!/web-\d+/.test(fresh.fetchCalls.join(" "))) failures += 1;

// 场景 2：localStorage 里有 id -> 应把服务端历史拉回来渲染，
// 并恢复这场对话自己的设置（这里设为开了工具，且因为说过话而锁死）
const restored = await scenario("2. 恢复历史 + 会话级设置（已锁）", {
  seedSession: "web-test-restore",
  sessionItems: { id: "web-test-restore", workspaceId: "ws-bc8da407",
                  // systemPrompt 是**会话记录里的值**（load_settings 取最后一轮）——
                  // 刷新之后面板要靠它恢复，而不是回到默认。
                  settings: { toolsEnabled: true, systemPrompt: "会话里在用的那份提示词" },
                  toolsLocked: true, items: storedItems },
  expectTools: true,
  expectLocked: true,
});
const checkRestored = (label, condition, detail = "") => {
  console.log(`  ${condition ? "✅" : "❌"} ${label}${detail ? "  " + detail : ""}`);
  if (!condition) failures += 1;
};
checkRestored("用了 localStorage 里的 id", restored.fetchCalls.some((u) => u.includes("web-test-restore")));
checkRestored("历史里的提问渲染出来了", restored.text.includes("历史里的第一个提问"));
checkRestored("历史里的回答渲染出来了", restored.text.includes("历史里的回答"));
checkRestored("助手回答里的 Markdown 列表被渲染", !!restored.document.querySelector(".markdown-content ul li"));
checkRestored("助手回答里的行内代码被渲染", !!restored.document.querySelector(".markdown-content code"));
checkRestored("助手回答里的 Markdown 表格被渲染", !!restored.document.querySelector(".markdown-content table th"));
checkRestored("表格单元格内容渲染正确", restored.document.querySelector(".markdown-content table td:nth-child(2)")?.textContent === "React 18 + esbuild");
checkRestored("代码块显示语言名", restored.document.querySelector(".markdown-code-language")?.textContent === "python");
const copyButton = restored.document.querySelector(".markdown-code-copy");
copyButton?.click();
await new Promise((resolve) => setTimeout(resolve, 30));
checkRestored("代码块复制按钮只复制代码内容", restored.clipboardWrites.at(-1) === "print(\"hi\")", `实得 ${JSON.stringify(restored.clipboardWrites.at(-1))}`);
checkRestored("用户消息保持原样文本，不走 Markdown", !restored.document.querySelector(".user-bubble strong"));
checkRestored("工具步骤也渲染出来了", restored.text.includes("run_bash"));

// ── 符号保真：不该被渲染的东西必须逐字留下 ─────────────────────────────────
//
// 这一组守的是"解析器吞字符"这类**只看得见复制结果**的 bug：星号被当成强调定界符
// 拿掉之后，界面照样正常，只有粘出来才发现 `src/chat/*.py` 变成了 `src/chat/.py`。
// 所以断言的不是"渲染成功"，而是**原文逐字仍在**。
const restoredMarkdownText = restored.document.querySelector(".markdown-content")?.textContent || "";
const fidelity = (label, expected) =>
  checkRestored(label, restoredMarkdownText.includes(expected), `实得 ${JSON.stringify(restoredMarkdownText.slice(-90))}`);
fidelity("路径通配 src/chat/*.py 逐字保留", "packages/chat/src/chat/*.py");
fidelity("通配 *.jsonl 逐字保留", "*.jsonl");
fidelity("乘法 2 * 3 逐字保留", "2 * 3");
fidelity("词内星号 4*5 逐字保留", "4*5");
fidelity("\\* 转义只留星号（反斜杠不显示）", "*字面星号*");
checkRestored("转义的反斜杠没有漏到界面上", !restoredMarkdownText.includes("\\*字面星号"));
checkRestored("正常的 **粗体** 仍然渲染", !!restored.document.querySelector(".markdown-content strong"));
// 提示词面板要恢复成**这个会话在用的那份**，不是回到默认 —— 以前提示词只活在
// React state 里，刷新就丢，而会话历史还在服务端，两边对不上。
//
// 面板默认是收起的（textarea 根本不在 DOM 里），所以先点开那个按钮再读。
const openPanel = [...restored.document.querySelectorAll("button")]
  .find((button) => button.textContent.trim() === "系统提示词");
openPanel?.click();
await new Promise((resolve) => setTimeout(resolve, 30));
const restoredPrompt = restored.document.querySelector(".system-panel textarea")?.value;
checkRestored("刷新后系统提示词从会话记录恢复（不是回到默认）",
              restoredPrompt === "会话里在用的那份提示词",
              `实得 ${JSON.stringify(restoredPrompt)}`);

// 场景 3：localStorage 不可用（不透明 origin）-> 仍须能渲染
await scenario("3. 存储不可用也不白屏", { withUrl: false });
// 场景 4：侧栏收起状态从 localStorage 恢复 -> 应缩成窄条，只留展开按钮
const rail = await scenario("4. 侧栏收起（缩成窄条）", { collapsed: true });
const railCheck = (label, condition) => {
  console.log(`  ${condition ? "✅" : "❌"} ${label}`);
  if (!condition) failures += 1;
};
railCheck("侧栏带上了 is-collapsed", /is-collapsed/.test(rail.html));
railCheck("窄条里只剩展开按钮（图标，不是文字）",
          rail.html.includes("sidebar-toggle") && !rail.html.includes("session-item"));
railCheck("窄条里不再渲染会话列表", !/侧栏里的会话标题/.test(rail.text));
railCheck("窄条里没有「新对话」", !rail.text.includes("新对话"));

// 场景 5：轮次进行中的进度轮询。GET /api/sessions/{id} 每次返回下一帧（模拟边跑边
// 落盘），/api/chat 永不返回（模拟"还在跑"）。断言两件事：步骤会**逐条**出现，
// 而且用户点开的工具输出不会被下一次轮询折叠回去（React key 必须跨轮询稳定）。
await scenario("5. 轮询进度（步骤逐条出现、展开状态不丢）", {
  seedSession: "web-progress",
  progressSequence: [
    { running: true, items: [{ kind: "user", content: "跑个命令" }] },
    {
      running: true,
      items: [
        { kind: "user", content: "跑个命令" },
        { kind: "step", tool: "run_bash", arguments: '{"command":"ls"}', result: "a.txt", ok: true },
      ],
    },
    {
      running: true,
      items: [
        { kind: "user", content: "跑个命令" },
        { kind: "step", tool: "run_bash", arguments: '{"command":"ls"}', result: "a.txt", ok: true },
        { kind: "running", tool: "read_file", arguments: '{"path":"a.txt"}' },
      ],
    },
  ],
});

// 场景 6：**前端那张 kind → 渲染 的映射表必须覆盖后端能产出的每一种 kind。**
//
// 取值域来自契约（后端 session_store.ITEM_KINDS 声明的），而且探针数据**由它驱动
// 生成** —— 这一点是重点：手写一份固定列表就成了"验我的意图，不是验代码"，后端加了
// 新 kind 时探针里根本没有那一项，测试照样绿（这个坑我在这套检查里踩了第三次）。
//
// 于是后端加一个新 kind 之后：契约更新 → 探针里多出一项 → 前端不认识 → 落到
// 「未知条目」→ 红。这正是"后端加了新类型而前端悄悄画错"那类静默 bug 的哨兵。
const SAMPLE_BY_KIND = {
  user: { kind: "user", content: "用户消息占位" },
  step: { kind: "step", tool: "run_bash", arguments: "{}", result: "ok", ok: true },
  running: { kind: "running", tool: "read_file", arguments: "{}" },
  "bash-request": { kind: "bash-request", id: "bashreq-probe", command: "printf hi", cwd: "/tmp/ws", timeout: 60, status: "executed", result: "bash-approved-output\n[exit code: 0]" },
  plan: { kind: "plan", todos: [{ content: "计划条目占位", status: "in_progress" }] },
  assistant: { kind: "assistant", content: "助手消息占位" },
};
// 每种 kind 渲染出来时文本里该出现什么 —— 要**积极的证据**（"没报未知条目"是消极
// 证据：整条渲染路径没跑也会得到它）。
const MARKER_BY_KIND = {
  user: "用户消息占位", step: "run_bash", running: "正在执行", "bash-request": "bash-approved-output", plan: "计划条目占位", assistant: "助手消息占位",
};
// 没有样本的 kind 用兜底：它必须让前端落到「未知条目」——**这就是探针的意图**，
// 也正是"后端加了新 kind 而前端还没学会"时该有的表现。
const probeItems = itemKinds
  .map((kind) => SAMPLE_BY_KIND[kind] ?? { kind, content: "（这个 kind 还没有样本）" })
  .sort((a, b) => (a.kind === "user" ? -1 : b.kind === "user" ? 1 : 0));

const kindsProbe = await scenario("6. item kind 覆盖（契约取值域驱动）", {
  seedSession: "web-kinds",
  sessionItems: {
    id: "web-kinds", workspaceId: "ws-bc8da407", settings: { toolsEnabled: false },
    toolsLocked: true, running: false, items: probeItems,
  },
  expectLocked: true,   // 这个 fixture 是「说过话的会话」，开关该锁死
});
const kindsCheck = (label, condition, detail = "") => {
  console.log(`  ${condition ? "✅" : "❌"} ${label}${detail ? "  " + detail : ""}`);
  if (!condition) failures += 1;
};
kindsCheck("契约里有 item_kind 取值域（否则这条测试是空的）",
           itemKinds.length > 0, `实得 ${JSON.stringify(itemKinds)}`);
// 先自检：**这个哨兵真的会响吗** —— 塞一个契约里没有的 kind，必须落到"未知条目"。
// 不然"没看到未知条目"可能只是因为整条渲染路径根本没跑。
const unknownProbe = await scenario("6b. 未知 kind 必须显形（哨兵自检）", {
  seedSession: "web-kinds-unknown",
  sessionItems: {
    id: "web-kinds-unknown", workspaceId: "ws-bc8da407", settings: { toolsEnabled: false },
    toolsLocked: true, running: false,
    items: [{ kind: "brand-new-kind-from-server", content: "x" }],
  },
  expectLocked: true,
});
kindsCheck("注入一个不认识的 kind → 界面明说「未知条目」（不是静默当普通消息画）",
           unknownProbe.text.includes("未知条目"), `实得 ${JSON.stringify(unknownProbe.text.slice(0, 80))}`);
kindsCheck("契约取值域渲染完没有落到未知条目（前端的映射表覆盖全了）",
           !kindsProbe.text.includes("未知条目"),
           `还没学会的 kind: ${JSON.stringify(probeItems.filter((i) => !MARKER_BY_KIND[i.kind]).map((i) => i.kind))}`);
// 积极的证据：每种有样本的 kind 都真的画出来了。写成"逐项检查"而不是一串 &&，
// 失败时能直接看出是**哪一个** kind 没画出来。
const missingMarkers = itemKinds.filter(
  (kind) => MARKER_BY_KIND[kind] && !kindsProbe.text.includes(MARKER_BY_KIND[kind]));
kindsCheck("每种 kind 都真的画出来了（不是「没报错」就算过）",
           missingMarkers.length === 0, `没画出来的: ${JSON.stringify(missingMarkers)}`);
const planPanel = kindsProbe.document.querySelector(".plan-panel");
kindsCheck("plan 渲染在输入区上方的独立面板", !!planPanel && !kindsProbe.document.querySelector(".chat-box .plan-panel"));
kindsCheck("plan 不再渲染进对话窗口", !kindsProbe.document.querySelector(".chat-box .plan-list"));
if (planPanel) {
  const wasOpen = planPanel.open;
  planPanel.querySelector("summary")?.click();
  const collapsed = planPanel.open === false;
  planPanel.querySelector("summary")?.click();
  kindsCheck("plan 面板可以折叠和展开", wasOpen === true && collapsed && planPanel.open === true);
}

const stalePlanProbe = await scenario("6c. 下一轮不显示上一轮 plan", {
  seedSession: "web-stale-plan",
  sessionItems: {
    id: "web-stale-plan", workspaceId: "ws-bc8da407",
    settings: { toolsEnabled: true }, toolsLocked: true, running: false,
    items: [
      { kind: "user", content: "上一轮" },
      { kind: "plan", todos: [{ content: "旧计划不该显示", status: "in_progress" }] },
      { kind: "assistant", content: "上一轮结束" },
      { kind: "user", content: "下一轮" },
      { kind: "assistant", content: "直接回答" },
    ],
  },
  expectTools: true,
  expectLocked: true,
});
kindsCheck("进入下一轮后不显示上一轮 plan",
           !stalePlanProbe.document.querySelector(".plan-panel") && !stalePlanProbe.text.includes("旧计划不该显示"));

// 场景 7：bash 审批接管输入端。
//
// 两条不变式：待审批时**输入端**是审批面板（发送按钮让位），而**对话流**里不该
// 冒出"Bash 请求"卡片 —— 审批是输入区的交互，不该把对话本身挤开。
await scenario("7. bash 审批（输入端接管）", {
  seedSession: "web-approval",
  sessionItems: {
    id: "web-approval", workspaceId: "ws-bc8da407",
    settings: { toolsEnabled: true }, toolsLocked: true, running: false,
    items: [
      { kind: "user", content: "跑个命令" },
      { kind: "assistant", content: "我来执行" },
      { kind: "bash-request", id: "bashreq-probe-1", command: "printf hi",
        cwd: "/tmp/ws", timeout: 60, status: "pending", result: "" },
    ],
  },
  expectTools: true, expectLocked: true, expectApproval: true,
});

// 场景 8：空正文的 assistant 条目不该画出空气泡。
//
// 这种记录是真实的：带 tool_calls 的轮次里，模型可能只说工具、不说人话，正文就是
// 空串（前后空白也算空）。画出来是一行什么都没有的气泡，看着像界面坏了。
const emptyAssistant = await scenario("8. 空 assistant 正文不画气泡", {
  seedSession: "web-empty-assistant",
  sessionItems: {
    id: "web-empty-assistant", workspaceId: "ws-bc8da407",
    settings: { toolsEnabled: true }, toolsLocked: true, running: false,
    items: [
      { kind: "user", content: "只调工具那一轮" },
      { kind: "assistant", content: "" },
      { kind: "assistant", content: "   \n  " },
      { kind: "step", tool: "run_bash", arguments: '{"command":"ls"}', result: "a.txt", ok: true },
      { kind: "assistant", content: "收尾说明" },
    ],
  },
  expectTools: true, expectLocked: true,
});
// 断言写成"数气泡个数"而不是"没看到空白"：空白本来就看不见，测不出来。
// 期望正好 3 个气泡 —— user + step + 收尾 assistant，两条空 assistant 都不占行。
const emptyAssistantBubbles = emptyAssistant.document.querySelectorAll(".chat-box .bubble").length;
kindsCheck("空正文的 assistant 不占气泡（只剩 user + step + 收尾）",
           emptyAssistantBubbles === 3, `实得 ${emptyAssistantBubbles} 个气泡`);
kindsCheck("没有渲染出空的 .markdown-content 壳",
           ![...emptyAssistant.document.querySelectorAll(".markdown-content")]
             .some((node) => !node.textContent.trim()));
kindsCheck("正常正文的 assistant 照常显示",
           emptyAssistant.text.includes("收尾说明"));

// 场景 9：行内 Markdown 边界 —— 该渲染的要渲染，不该动的要逐字别动。
//
// 这一组守的是两类**只有复制出来才发现**的 bug，它们不报错、不白屏，只是安静地显示错：
//   1. **吞字符**：`a*b 与 c*(d)`、`长*宽…2*(`、`$E = m*c^2$` 里的星号被当成强调
//      定界符配成一对，于是从第一个星号一路吃到第二个，用户复制路径/公式就少字符；
//   2. **该渲染不渲染**：`这是*强调*文字` 这种中文行内强调退化成字面量 —— 中文不加
//      空格，任何"定界符外侧必须是空白"的规则都会把它判死。
// 一个用例 = 一个 assistant 条目，这样能逐条断言"文本 + 有没有 <em>/<strong>"，
// 而不是只对整段做一次 includes（那样某一条悄悄坏了根本看不出来）。
const MD_EDGE_CASES = [
  // [输入, 期望的可见文本, 期望的强调标签(""|"em"|"strong")]
  ["3*4", "3*4", ""],
  ["3*4 和 5*6", "3*4 和 5*6", ""],
  ["a*b 与 c*(d)", "a*b 与 c*(d)", ""],
  ["面积 = 长*宽，对角线 = 2*(长+宽)", "面积 = 长*宽，对角线 = 2*(长+宽)", ""],
  ["2*3 and 4*5", "2*3 and 4*5", ""],
  ["5*6*78", "5*6*78", ""],
  ["P = 2*pi*r，A = pi*r**2", "P = 2*pi*r，A = pi*r**2", ""],
  ["通配路径 packages/chat/src/chat/*.py 与 *.json", "通配路径 packages/chat/src/chat/*.py 与 *.json", ""],
  // 公式：`$...$` 区间不透明，所以里面两颗星号也不会被配对（纯靠星号规则救不了这条）。
  ["设 $E = m*c^2$，且 $x \\in \\mathbb{R}^{*}$", "设 $E = m*c^2$，且 $x \\in \\mathbb{R}^{*}$", ""],
  ["$x^{*}$ 与 $y^{*}$", "$x^{*}$ 与 $y^{*}$", ""],
  // `$` 也是货币符号：不成对就整个当字面量，不能被误认成公式。
  ["价格 $5 到 $10 之间", "价格 $5 到 $10 之间", ""],
  // 强调：中文行内（两侧都是汉字）、英文、混排三种都要活。
  ["这是*强调*文字", "这是强调文字", "em"],
  ["这是**强调**文字", "这是强调文字", "strong"],
  ["**粗体**文字", "粗体文字", "strong"],
  ["see *note* here", "see note here", "em"],
  ["**bold** text", "bold text", "strong"],
  ["中文*english*中文", "中文english中文", "em"],
  ["*斜体*.", "斜体.", "em"],
  // 混排强调：中文句子里夹数字/英文的粗体，是中文模型输出的最高频形状。
  // 这几条守的是一个真踩过的坑 —— 曾经要求"开合两个星号必须同为 ASCII 邻接"，
  // 结果 `**P99 延迟**`（开贴 ASCII、闭贴汉字）被判成字面量，
  // 而纯 ASCII（`**P99**`）和纯中文（`**延迟**`）都正常。混排才坏，所以必须单独钉。
  ["**P99 延迟**", "P99 延迟", "strong"],
  ["关键指标是 **P99 延迟**,现在约 320 ms,目标是压到 200 ms 以内。",
   "关键指标是 P99 延迟,现在约 320 ms,目标是压到 200 ms 以内。", "strong"],
  ["**配置迁移**:新配置已上线。", "配置迁移:新配置已上线。", "strong"],
  ["单测覆盖率 *82%*,还差一点。", "单测覆盖率 82%,还差一点。", "em"],
  // 反向的混排（汉字开头、ASCII 结尾）是**已知不支持**的，见 README —— 这里钉住
  // "别退化成吞字符"：星号必须原样留着。
  ["**延迟 P99**", "**延迟 P99**", ""],
  // 幂运算符 `**` 不能被当成粗体定界符：它贴着 ASCII 字母/数字。
  ["2**(a+b) 与 x**(c)", "2**(a+b) 与 x**(c)", ""],
  // 行内码仍然最优先：里面的星号天生不解析。
  ["`长*宽` 与 `2*(a+b)`", "长*宽 与 2*(a+b)", ""],
];
const MD_DISPLAY_MATH = "$$\n\\begin{align} a &= b \\\\ c &= d \\end{align}\n$$";

const mdEdge = await scenario("9. 行内边界（乘法/路径/公式/强调）", {
  seedSession: "web-md-edge",
  sessionItems: {
    id: "web-md-edge", workspaceId: "ws-bc8da407",
    settings: { toolsEnabled: false }, toolsLocked: true, running: false,
    items: [
      { kind: "user", content: "边界用例" },
      ...MD_EDGE_CASES.map(([input]) => ({ kind: "assistant", content: input })),
      { kind: "assistant", content: MD_DISPLAY_MATH },
    ],
  },
  expectLocked: true,
});
// user 条目不走 markdown（那是刻意的边界），所以 .markdown-content 只对应 assistant 条目。
const mdNodes = [...mdEdge.document.querySelectorAll(".chat-box .markdown-content")];
kindsCheck("边界用例都渲染出来了（条数对得上）",
           mdNodes.length === MD_EDGE_CASES.length + 1,
           `期望 ${MD_EDGE_CASES.length + 1}，实得 ${mdNodes.length}`);
MD_EDGE_CASES.forEach(([input, wantText, wantTag], i) => {
  const node = mdNodes[i];
  const text = node?.textContent ?? "";
  const tag = node?.querySelector("em") ? "em" : node?.querySelector("strong") ? "strong" : "";
  kindsCheck(`#${String(i + 1).padStart(2, "0")} ${JSON.stringify(input)}`,
             text === wantText && tag === wantTag,
             `实得 ${JSON.stringify(text)} [${tag}]`);
});
const mathNode = mdNodes[MD_EDGE_CASES.length];
kindsCheck("行间公式渲染成独立的 math-block",
           !!mdEdge.document.querySelector(".chat-box .markdown-math-block"));
kindsCheck("行间公式原样保留（含 \\\\ 换行符与换行）",
           mathNode?.textContent === MD_DISPLAY_MATH,
           `实得 ${JSON.stringify(mathNode?.textContent)}`);

// 场景 10：复制按钮（每条消息一个，图标按钮，在气泡下面）。
//
// 为什么单列一个场景：复制这条路**从界面上看不出对错** —— 按下去没反应、复制到
// 空字符串、把内容悄悄"清理"过一遍，这三种都不报错、不白屏，只有粘出来才发现。
//
// 硬语义：**复制结果必须与日志原文逐字一致，也就是模型当时读到的那一份。**
// 工具输出超标时，模型读到的是「中间被掐掉的预览 + spill 定位符」，它得靠那行地址
// 自己再去 grep / read_file 取关键段落 —— 所以定位符**必须在复制结果里**；删掉它，
// 复制出来的就不再是"模型读到的东西"。全文在 /tmp/chat-spill/ 下，前端没有任何
// 接口能读它，所以"把完整内容捞进来"在这条路上也不可能发生。
const SPILL_RESULT = "$ cat big.log\nline1\n\n...[4096 chars omitted]...\n\nline9\n[full output: 8123 chars saved to /tmp/chat-spill/2026-09-18/001122-abc123.log — the middle above was elided. Read it with read_file (page it with offset/limit), or grep/sed it with run_bash.]\n[exit code: 0]";
const copyScene = await scenario("10. 复制（每条消息一个）", {
  seedSession: "web-copy",
  sessionItems: {
    id: "web-copy", workspaceId: "ws-bc8da407",
    settings: { toolsEnabled: true }, toolsLocked: true, running: false,
    items: [
      { kind: "user", content: "帮我看看日志" },
      { kind: "assistant", content: "先跑一下" },
      { kind: "step", tool: "run_bash", arguments: '{"command":"cat big.log"}',
        result: SPILL_RESULT, ok: true },
      { kind: "assistant", content: "日志里 line9 是异常点。" },
      { kind: "user", content: "下一个问题" },
      { kind: "assistant", content: "第二个回答" },
    ],
  },
  expectTools: true, expectLocked: true,
});
const copyCheck = (label, condition, detail = "") => {
  console.log(`  ${condition ? "✅" : "❌"} ${label}${detail ? "  " + detail : ""}`);
  if (!condition) failures += 1;
};
const rows = [...copyScene.document.querySelectorAll(".chat-box .message-row")];
const copyButtonsIn = (row) => [...row.querySelectorAll(".message-action")];
const toolCopyIn = (row) => [...row.querySelectorAll(".tool-step-copy")];
// 消息级操作条只属于**消息**（user / assistant）：下标 2 是工具步骤，没有。
copyCheck("消息级复制图标只出现在消息行上（工具行没有）",
          JSON.stringify(rows.map((row) => copyButtonsIn(row).length)) === JSON.stringify([1, 1, 0, 1, 1, 1]),
          `实得 ${JSON.stringify(rows.map((row) => copyButtonsIn(row).length))}`);
// 操作条里**只有一个**控件 —— 不再有"单条 + 整段"两个按钮那种设计。
// 按子元素个数判，不查具体类名：那个被删掉的类名不该留在测试里当引用。
copyCheck("每条操作条里只有一个控件（不再有单独的「复制整段」）",
          [...copyScene.document.querySelectorAll(".message-actions")]
            .every((bar) => bar.children.length === 1),
          `实得 ${JSON.stringify([...copyScene.document.querySelectorAll(".message-actions")].map((bar) => bar.children.length))}`);
copyCheck("工具行改用代码块那种复制按钮（不是消息气泡那套）",
          toolCopyIn(rows[2]).length === 1 && toolCopyIn(rows[2])[0].textContent.trim() === "复制",
          `实得 ${JSON.stringify(toolCopyIn(rows[2]).map((el) => el.textContent.trim()))}`);
copyCheck("工具行的复制按钮长在头部行里（summary 内）",
          !!rows[2]?.querySelector("summary .tool-step-copy"));
// 位置：操作条必须是气泡的**下一个兄弟**（在下面），不是浮在右上角。
const firstRow = rows[0];
copyCheck("操作条在气泡下面（DOM 顺序上紧跟气泡）",
          firstRow?.children.length === 2 &&
          firstRow.children[0].classList.contains("bubble") &&
          firstRow.children[1].classList.contains("message-actions"),
          `实得 ${JSON.stringify([...(firstRow?.children || [])].map((el) => el.className))}`);
// 图标按钮：复制前是 copy 图标，成功后换成对勾（上游就是这么做的）
const firstButton = copyButtonsIn(rows[0])[0];
copyCheck("按钮是图标按钮（28px 点击区，无文字）",
          firstButton?.textContent.trim() === "" && !!firstButton?.querySelector("svg"),
          `实得 ${JSON.stringify(firstButton?.textContent)}`);

// 单条复制：点 user 那一行
firstButton?.click();
await new Promise((resolve) => setTimeout(resolve, 30));
copyCheck("单条复制：user 提问写进剪贴板", copyScene.clipboardWrites.at(-1) === "帮我看看日志",
          `实得 ${JSON.stringify(copyScene.clipboardWrites.at(-1))}`);
copyCheck("复制成功后按钮显示对勾（图标换了，文案没换）",
          firstButton?.getAttribute("title") === "已复制",
          `实得 ${JSON.stringify(firstButton?.getAttribute("title"))}`);

// 工具块的复制按钮**只在展开时出现**。jsdom 不算样式（也不加载外链 CSS），所以
// 这条只能查样式表**文本**——和前面那条 .row-action / display:none 的做法一样。
// 两条规则要成对存在：折叠时隐藏、[open] 时显示。
const allCss = [...html.matchAll(/<link[^>]+href="\/static\/([^"]+\.css)"/g)]
  .map((m) => readFileSync(new URL(m[1], STATIC_DIR), "utf-8"))
  .join("\n")
  .replace(/\/\*[\s\S]*?\*\//g, "");
copyCheck("折叠时隐藏工具复制按钮（CSS 规则成对存在之一）",
          /\.tool-step summary \.tool-step-copy\s*\{[^}]*display:\s*none/.test(allCss));
copyCheck("展开时显示工具复制按钮（CSS 规则成对存在之二）",
          /\.tool-step\[open\] summary \.tool-step-copy\s*\{[^}]*display:\s*inline-flex/.test(allCss));

// 单条复制工具输出：走工具块**自己的**头部按钮，必须**逐字等于**日志里那一份
// （含 spill 定位符与省略标记），而且**不能顺手把输出折叠掉** —— 按钮长在
// <summary> 里，不拦事件冒泡的话点一下复制就会连带触发展开/收起。
//
// 这里手动先展开：按 CSS，按钮折叠时是看不见的（jsdom 不执行那条），所以测试按
// 用户的真实动作来 —— 先点开工具行，再点复制。
const toolDetails = rows[2]?.querySelector("details.tool-step");
if (toolDetails) {
  toolDetails.open = true;
}
toolCopyIn(rows[2])[0]?.click();
await new Promise((resolve) => setTimeout(resolve, 30));
copyCheck("工具块复制 = 模型读到的那份原文（定位符与省略标记都在）",
          copyScene.clipboardWrites.at(-1) === SPILL_RESULT,
          `实得 ${JSON.stringify(String(copyScene.clipboardWrites.at(-1)).slice(-80))}`);
copyCheck("点工具块的复制不会把输出折叠掉（事件没冒泡到 summary）",
          toolDetails?.open === true,
          `点之后 open=${toolDetails?.open}`);
copyCheck("工具块复制按钮显示「已复制」",
          toolCopyIn(rows[2])[0]?.textContent.trim() === "已复制",
          `实得 ${JSON.stringify(toolCopyIn(rows[2])[0]?.textContent.trim())}`);

// 助手那一条：**复制的是它所在的整个 turn**（含工具调用与输出），不是那一句正文。
// 这是本轮修的核心：曾经拆成"单条 + 复制整段"两个按钮，结果点助手回复那个图标
// 只拿到正文，工具调用丢了 —— 而那是最自然的动作。
copyButtonsIn(rows[3])[0]?.click();
await new Promise((resolve) => setTimeout(resolve, 30));
const turnText = copyScene.clipboardWrites.at(-1) || "";
const EXPECTED_TURN = ["先跑一下", SPILL_RESULT, "日志里 line9 是异常点。"].join("\n\n");
copyCheck("点助手的复制 = 整轮（助手正文 + 工具输出，逐字拼接）",
          turnText === EXPECTED_TURN,
          `实得 ${JSON.stringify(turnText.slice(0, 60))}…`);
copyCheck("整轮里含工具输出全文（含 spill 定位符）",
          turnText.includes("[full output: 8123 chars saved to /tmp/chat-spill/"));
copyCheck("整轮不含提问（提问是另一个气泡的事）", !turnText.includes("帮我看看日志"));
copyCheck("整轮不含下一段的内容", !turnText.includes("第二个回答"));
// 同一段里的**每一条**助手条目复制出来都是这一整轮 —— 行为不取决于点的是哪一条
copyButtonsIn(rows[1])[0]?.click();
await new Promise((resolve) => setTimeout(resolve, 30));
copyCheck("同一段的另一条助手条目也复制整轮（不取决于点哪一条）",
          copyScene.clipboardWrites.at(-1) === EXPECTED_TURN,
          `实得 ${JSON.stringify(String(copyScene.clipboardWrites.at(-1)).slice(0, 40))}…`);
// 第二段的助手条目只含它自己那一轮
copyButtonsIn(rows[5])[0]?.click();
await new Promise((resolve) => setTimeout(resolve, 30));
copyCheck("第二段的助手条目只含它自己那一轮",
          copyScene.clipboardWrites.at(-1) === "第二个回答",
          `实得 ${JSON.stringify(copyScene.clipboardWrites.at(-1))}`);
copyButtonsIn(rows[4])[0]?.click();
await new Promise((resolve) => setTimeout(resolve, 30));
copyCheck("user 行复制的是提问原文（不带助手内容）",
          copyScene.clipboardWrites.at(-1) === "下一个问题",
          `实得 ${JSON.stringify(copyScene.clipboardWrites.at(-1))}`);

// 回退路径：手机上（http + 非 localhost）拿不到 navigator.clipboard，必须不抛异常地降级。
Object.defineProperty(copyScene.document.defaultView.navigator, "clipboard", {
  configurable: true, value: undefined,
});
let fallbackThrew = false;
try {
  copyButtonsIn(rows[0])[0]?.click();
  await new Promise((resolve) => setTimeout(resolve, 30));
} catch {
  fallbackThrew = true;
}
copyCheck("没有 clipboard API 时降级而不是抛异常", !fallbackThrew);
copyCheck("降级失败时按钮报错（不静默假装成功）",
          copyButtonsIn(rows[0])[0]?.getAttribute("title") === "复制失败",
          `实得 ${JSON.stringify(copyButtonsIn(rows[0])[0]?.getAttribute("title"))}`);

// 场景 10b：一段 loop **以工具调用收尾**（助手最后没再说话）。
//
// 这时助手的复制仍然要带上工具调用 —— 段的末尾是工具块，而工具行没有消息操作条，
// 所以复制按钮留在助手回复那一行也是对的：它复制的是**整段**，不看末尾长什么样。
const tailToolScene = await scenario("10b. 助手复制带上工具调用（以工具收尾）", {
  seedSession: "web-copy-tail",
  sessionItems: {
    id: "web-copy-tail", workspaceId: "ws-bc8da407",
    settings: { toolsEnabled: true }, toolsLocked: true, running: false,
    items: [
      { kind: "user", content: "跑一下" },
      { kind: "assistant", content: "我来跑" },
      { kind: "step", tool: "run_bash", arguments: '{"command":"ls"}', result: "$ ls\na.txt\n[exit code: 0]", ok: true },
    ],
  },
  expectTools: true, expectLocked: true,
});
const tailRows2 = [...tailToolScene.document.querySelectorAll(".chat-box .message-row")];
copyCheck("工具行没有消息级图标（它自己的复制在头部行里）",
          tailRows2.length === 3 &&
          tailRows2[1]?.querySelectorAll(".message-action").length === 1 &&
          tailRows2[2]?.querySelectorAll(".message-action").length === 0,
          `实得 ${JSON.stringify(tailRows2.map((row) => row.querySelectorAll(".message-action").length))}`);
// 点助手那个复制：拿到的是「助手正文 + 工具输出」，不含提问
[...tailRows2[1].querySelectorAll(".message-action")][0]?.click();
await new Promise((resolve) => setTimeout(resolve, 30));
copyCheck("以工具收尾时，助手复制 = 助手正文 + 工具输出（不含提问）",
          tailToolScene.clipboardWrites.at(-1) === "我来跑\n\n$ ls\na.txt\n[exit code: 0]",
          `实得 ${JSON.stringify(tailToolScene.clipboardWrites.at(-1))}`);

console.log();
if (failures) {
  console.log(`失败 ${failures} 项`);
  process.exit(1);
}
console.log("UI 冒烟测试通过（12 个场景）");
