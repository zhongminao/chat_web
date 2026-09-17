// 把一条展示条目折成「可复制的文本」：一个问题 / 一条助手回复 / 一个工具步骤的输出。
//
// **唯一的语义：复制出来的就是日志里的原文，也就是模型当时看到的那些字符。**
// 这里不做任何清理、截断或美化，连"看起来像噪音"的东西也不动 —— 一旦开始"帮"
// 用户删东西，复制的内容就不再是"模型看到的那一份"，也就没法用它复盘"模型当时
// 到底看到了什么"。spill 的定位符（`[full output: N chars saved to /tmp/chat-spill/...]`）
// 正是这一类：它是模型**确实看到过**的一行（内容超标时模型读到的就是截断预览 +
// 这行地址，它得靠这个路径自己去 grep / read_file 取关键段落），所以既不该删掉它、
// 也不该去把全文捞回来 —— 那样复制出来的就不再是模型看到的东西了。

export function messageToText(message) {
  if (!message) {
    return "";
  }
  // 工具步骤与跑完的 bash 请求：正文就是输出本身。命令通常已经在输出里了
  // （run_bash 的输出以 `$ <命令>` 开头，read_file 以 `[read_file] <路径>` 开头），
  // 所以这里不再另加"⚙ 工具名"前缀 —— 加了就是同一件事说两遍。
  if (message.role === "tool-step" || message.role === "bash-request") {
    return String(message.result ?? "");
  }
  // 正在跑的那个工具：还没有结果，没什么可复制。
  if (message.role === "tool-running") {
    return "";
  }
  // 未知条目：前端不认识它，复制不出东西来（宁可为空，也不要编一段出来）。
  if (message.role === "unknown") {
    return "";
  }
  return String(message.content ?? "");
}

// 一次 loop = **助手那一轮的全部产出**：它说了什么 + 它调了哪些工具。
//
// 边界只能靠 user 条目认出来：一条 user 之后、下一条 user 之前，中间那些条目就是
// 那一轮的助手侧。**提问本身不算在内** —— 界面上左右两个气泡是两种东西，用户要
// 复制的"这一轮 agent 干了什么"不该把问题也卷进来。
//
// 返回的是每一段的起止下标（只覆盖助手侧，所以 start 指向 user 之后的第一个条目）。
export function assistantTurnRanges(messages) {
  const ranges = [];
  let current = null;
  (messages || []).forEach((message, index) => {
    if (message.role === "user") {
      current = null;      // 提问既结束上一段，也不属于任何一段助手侧
      return;
    }
    if (!current) {
      current = { start: index, end: index };
      ranges.push(current);
    } else {
      current.end = index;
    }
  });
  return ranges;
}

/** 一段助手 loop 的文本：各条目之间空一行；空条目（plan、正在跑的工具）自然丢掉。 */
export function assistantTurnToText(messages) {
  return (messages || [])
    .map(messageToText)
    .filter(Boolean)
    .join("\n\n");
}
