/* UI 冒烟测试：把真实产物 app.js 放进 jsdom 里跑一遍，确认它真能渲染。
 *
 * 存在的理由：这个环境里没有浏览器，前端改动此前完全靠人肉点。而 React 的失败
 * 方式很安静（白屏 / 一个空 div），光看接口返回发现不了。这里用 jsdom 把
 * index.html + app.js 真跑一遍，断言 #root 里出现了预期内容。
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

const dom = new JSDOM(html, { runScripts: "outside-only", pretendToBeVisual: true });
const { window } = dom;

// 组件挂载时会 fetch /api/providers；给它一个替身，否则请求永远挂着。
// 顺手记下调用，用来断言"挂载副作用真的跑了" —— 只判 fetch !== undefined 是
// 空转的（那是我自己赋的值，永远为真）。
const fetchCalls = [];
window.fetch = (url) => {
  fetchCalls.push(String(url));
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve(String(url).includes("providers") ? providers : {}),
  });
};

const failures = [];
function check(label, condition, detail = "") {
  console.log(`  ${condition ? "✅" : "❌"} ${label}${detail ? "  " + detail : ""}`);
  if (!condition) failures.push(label);
}

try {
  window.eval(bundle);
} catch (error) {
  console.log(`  ❌ bundle 执行抛出异常: ${error.message}`);
  process.exit(1);
}

// React 18 的 render 是异步的，等一轮微任务 + 一个宏任务
await new Promise((resolve) => setTimeout(resolve, 50));

const root = window.document.getElementById("root");
const text = root.textContent || "";
const html2 = root.innerHTML || "";

console.log("=== 渲染结果 ===");
check("#root 非空（没白屏）", html2.length > 0, `${html2.length} 字符`);
check("标题渲染出来", text.includes("AI 聊天助手"));
check("输入框存在", !!window.document.querySelector("textarea, input[type=text]"));
check("挂载时请求了供应商目录", fetchCalls.some((u) => u.includes("/api/providers")),
      fetchCalls.join(", ") || "一次都没调用");

console.log();
if (failures.length) {
  console.log(`失败 ${failures.length} 项: ${failures.join(", ")}`);
  process.exit(1);
}
console.log("UI 冒烟测试通过");
