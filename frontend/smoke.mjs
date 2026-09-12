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

const STATIC_DIR = new URL("../chat/static/", import.meta.url);
const html = readFileSync(new URL("index.html", STATIC_DIR), "utf-8");
const bundle = readFileSync(new URL("app.js", STATIC_DIR), "utf-8");

const providers = {
  providers: [
    { provider: "deepseek", display_name: "DeepSeek",
      models: [{ id: "deepseek-flash", name: "DeepSeek Flash", temperature: 0.2 }] },
  ],
  default_provider: "deepseek",
  default_model: "deepseek-flash",
  default_system_prompt: "You are a helpful assistant.",
  tool_system_prompt: "You are an agent that can take real actions through tools.",
};

/* 服务端会返回的历史（渲染顺序：user / step / assistant 交替）。 */
const storedItems = [
  { kind: "user", content: "历史里的第一个提问" },
  { kind: "step", tool: "run_bash", arguments: '{"command":"echo hi"}',
    result: "$ echo hi\nhi\n[exit code: 0]", ok: true },
  { kind: "assistant", content: "历史里的回答" },
];

/* 侧栏用的会话列表。 */
const workspacePayload = {
  workspaces: [
    { id: "ws-bc8da407", name: "chat", root: "/home/zhong/mydisk/tools/chat" },
    { id: "ws-8c393341", name: "tmp", root: "/tmp" },
  ],
  default: "ws-bc8da407",
};

const sessionList = {
  workspace: workspacePayload.workspaces[0],
  sessions: [
    { id: "web-test-restore", title: "侧栏里的会话标题", turns: 3,
      lastActivity: Date.now(), workspaceId: "ws-bc8da407" },
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

async function scenario(name, { withUrl = true, seedSession = null, sessionItems = null, collapsed = false, expectTools = false, expectLocked = false } = {}) {
  const pageErrors = [];
  const fetchCalls = [];
  const patched = [];

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
    let body = providers;
    if (target.includes("/api/browse")) {
      body = browsePayload;
    } else if (target.includes("/api/workspaces")) {
      body = workspacePayload;
    } else if (target.includes("/api/sessions/")) {
      body = sessionItems ?? {};
    } else if (target.includes("/api/sessions")) {
      body = sessionList;
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
  check("输入框存在", !!window.document.querySelector("textarea, input[type=text]"));
  check("请求了供应商目录", fetchCalls.some((u) => u.includes("/api/providers")));
  // 覆盖 providers -> state -> 渲染 这条链：接口回来了要真的显示到 chip 上
  check("模型 chip 显示出接口返回的模型名", text.includes("DeepSeek Flash"));
  check("发送按钮在", !!window.document.querySelector("button[type=submit]"));
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
  }

  // 点一下开关：没锁的话应该把新状态 PATCH 回这场对话；锁了就不该发生任何写回。
  // 只渲染不点的话，上面那条断言证明不了写回这条路是通的。
  // 不去核对具体 id：新会话的 id 是当场随机生成的，抓不到。
  toolsBox?.click();
  await new Promise((resolve) => setTimeout(resolve, 20));
  const writeBack = patched.find((call) => /\/api\/sessions\/.+/.test(call.url));
  let wroteTools = null;
  try {
    wroteTools = writeBack ? JSON.parse(writeBack.body).toolsEnabled : null;
  } catch (error) {
    wroteTools = "解析失败";
  }
  if (expectLocked) {
    check("锁了的对话，点开关不会写回", writeBack === undefined,
          writeBack ? `却 PATCH 了 ${writeBack.url.replace(/^.*\/api/, "/api")}` : "");
  } else {
    check("点开关会把新状态写回这场对话",
          wroteTools === !expectTools,
          `PATCH ${writeBack ? writeBack.url.replace(/^.*\/api/, "/api") : "(没发生)"} body.toolsEnabled=${wroteTools}`);
  }

  if (pageErrors.length) {
    console.log("  --- 页面报错 ---");
    for (const line of pageErrors.slice(0, 3)) {
      console.log("    " + line.split("\n")[0].slice(0, 160));
    }
  }
  return { text, html: html2, fetchCalls };
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
                  settings: { toolsEnabled: true }, toolsLocked: true, items: storedItems },
  expectTools: true,
  expectLocked: true,
});
const checkRestored = (label, condition) => {
  console.log(`  ${condition ? "✅" : "❌"} ${label}`);
  if (!condition) failures += 1;
};
checkRestored("用了 localStorage 里的 id", restored.fetchCalls.some((u) => u.includes("web-test-restore")));
checkRestored("历史里的提问渲染出来了", restored.text.includes("历史里的第一个提问"));
checkRestored("历史里的回答渲染出来了", restored.text.includes("历史里的回答"));
checkRestored("工具步骤也渲染出来了", restored.text.includes("run_bash"));

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

console.log();
if (failures) {
  console.log(`失败 ${failures} 项`);
  process.exit(1);
}
console.log("UI 冒烟测试通过（4 个场景）");
