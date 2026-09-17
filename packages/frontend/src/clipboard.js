// 复制到剪贴板。**必须有回退路径**，原因不是兼容老浏览器，而是安全上下文：
//
// `navigator.clipboard` 只在「安全上下文」里存在 —— HTTPS 或 localhost。而这个服务
// 是**纯 HTTP**（uvicorn 没配 ssl），局域网里是用 `http://10.23.x.x:8200` 打开的，
// 那个 origin 拿不到 `navigator.clipboard`。所以只写 clipboard API 的话，手机上点
// 「复制」会**静默什么都不发生**（既不复制、也不报错），比没有这个按钮更糟。
//
// 回退用 `document.execCommand("copy")`：已废弃，但所有浏览器都还支持，而且
// **不要求安全上下文**。这就是它在 2026 年还没被删掉的原因。
export async function copyText(text) {
  const value = String(text ?? "");
  if (!value) {
    return false;
  }

  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {
      // 权限被拒 / 非聚焦文档 —— 落到下面的回退，别直接失败。
    }
  }

  try {
    const area = document.createElement("textarea");
    area.value = value;
    area.setAttribute("readonly", "");
    // 放在视口外、且不可见：留在页面里会让整页跳一下滚动位置。
    area.style.position = "fixed";
    area.style.top = "-1000px";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    // iOS Safari 只认 select() + setSelectionRange 一起用，缺一个就选不中。
    area.setSelectionRange(0, value.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}
