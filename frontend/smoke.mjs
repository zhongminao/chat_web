/* UI 冒烟测试：把真实产物 app.js 放进 jsdom 里跑，确认它真能渲染。
 *
 * 存在的理由：这个环境里没有浏览器，前端改动此前只能靠人肉点。而 React 的失败方式
 * 很安静（白屏 / 一个空 div），光看接口返回发现不了。
 *
 * 三个场景，各自是一个新 JSDOM：
 *   1. 全新会话   —— 能渲染、会自己生成 sessionId
 *   2. 恢复历史   —— localStorage 里有 id 时，把服务端的历史拉回来并渲染出来
 *   3. 存储不可用 —— localStorage 抛异常时**仍然能渲染**（不能白屏）
 *
 * 刻意只做"能不能起来"这一层：不测交互、不测样式。够抓住"bundle 坏了/组件抛了"
 * 这类事故，也就是重构时最常见的那种。
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

let failures = 0;

async function scenario(name, { withUrl = true, seedSession = null, sessionItems = null } = {}) {
  const pageErrors = [];
  const fetchCalls = [];

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

  window.fetch = (url) => {
    const target = String(url);
    fetchCalls.push(target);
    const body = target.includes("/api/sessions/")
      ? (sessionItems ?? {})
      : providers;
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
  check("标题渲染出来", text.includes("AI 聊天助手"));
  check("输入框存在", !!window.document.querySelector("textarea, input[type=text]"));
  check("请求了供应商目录", fetchCalls.some((u) => u.includes("/api/providers")));
  // 覆盖 providers -> state -> 渲染 这条链：接口回来了要真的显示到 chip 上
  check("模型 chip 显示出接口返回的模型名", text.includes("DeepSeek Flash"));
  check("发送按钮在", !!window.document.querySelector("button[type=submit]"));

  if (pageErrors.length) {
    console.log("  --- 页面报错 ---");
    for (const line of pageErrors.slice(0, 3)) {
      console.log("    " + line.split("\n")[0].slice(0, 160));
    }
  }
  return { text, fetchCalls };
}

// 场景 1：全新会话
const fresh = await scenario("1. 全新会话");
console.log(`  ${/web-\d+/.test(fresh.fetchCalls.join(" ")) ? "✅" : "❌"} 自己生成了 sessionId 并去取历史`);
if (!/web-\d+/.test(fresh.fetchCalls.join(" "))) failures += 1;

// 场景 2：localStorage 里有 id -> 应把服务端历史拉回来渲染
const restored = await scenario("2. 恢复历史（刷新不丢对话）", {
  seedSession: "web-test-restore",
  sessionItems: { id: "web-test-restore", items: storedItems },
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

console.log();
if (failures) {
  console.log(`失败 ${failures} 项`);
  process.exit(1);
}
console.log("UI 冒烟测试通过（3 个场景）");
