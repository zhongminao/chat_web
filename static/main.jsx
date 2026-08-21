const { useState, useEffect } = React;
/* 从对象里把几个属性拎出来变成独立变量 等价于
  const useState = React.useState;
  const useEffect = React.useEffect; 
*/

/*   {}：在 JSX 里"切回 JS"
  JSX 里遇到 {，就是说"括号里这段按 JavaScript 算，把结果显示出来"：
  <div className="bubble">
    {message.content}      ← 显示 message.content 这个变量的值
  </div>
  <h1>AI 聊天助手</h1>      ← 纯文本，不用括号
*/

/*  

*/

function createId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}




// App 是一个函数，返回的东西长得像 HTML，这就叫 JSX。最后两行把它塞进页面jsx

function App() {
  {/* useState 会触发重绘的变量 最重要的一个东西！
    const [messages, setMessages] = useState([]);
            ↑读的        ↑写的           ↑初始值
    messages 是当前值，初始是空数组 []
    setMessages(新值) 用来改它
    改了之后 React 会自动重新执行一次 App()，界面跟着更新
    如果不使用useState，直接let messages = [] 赋值，然后messages = ['新消息']，
    React 不会知道新变量，它不会重新渲染界面，屏幕上还是空的
    因此 const [messages, setMessages] = [a, b]; 只是简单的两个赋值
    但是 const [messages, setMessages] = useState([]); 进行了“引用绑定 + 持久化”
    存储（useState 内部）：那个 []（空数组）并没有直接给 messages，
    而是被 React 内部的一个“状态表”（Fiber 节点）
    拿走了。这个“状态表”是 React 自己用 JS 对象在内存里维护的，跟解构赋值语法无关。
    简单来说 JS 的解构语法const [messages, setMessages] 只管“怎么接住”（解构赋值），
    React 机制管“存在哪”和“怎么更新”（状态表）。
  */}
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [providers, setProviders] = useState([]);
  const [provider, setProvider] = useState("");
  const [modelName, setModelName] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [defaultSystemPrompt, setDefaultSystemPrompt] = useState("");
  const [isSystemPanelOpen, setIsSystemPanelOpen] = useState(true);
  useEffect(() => {
    fetch("/api/providers")
      .then((response) => response.json())
      .then((data) => {
        const list = data.providers || [];
        setProviders(list);
        if (data.default_system_prompt) {              // ← 新增这 3 行
          setDefaultSystemPrompt(data.default_system_prompt);
          setSystemPrompt(data.default_system_prompt);
        }
        if (list.length > 0) {
          setProvider(list[0].provider);
          setModelName(list[0].models?.[0]?.id || "");
        }
      })
      .catch(() => {});
  }, []);
  {/* useEffect(() => { ... }, []); JS语法(除了调用了React的useEffect函数外)
        () => { ... }, []   ( ) 表示这个函数不需要传参，{ ... } 里面是函数要执行的代码，
        第二个[]代表app执行了一次之后，再次被调用就不去获得这些数据了
        吃饭(烤肉, [])，吃饭 函数看到第二个参数是空数组，就决定“只给你上一盘烤肉，后面不再加了”
      
  */}
  const canSend = input.trim() && !isLoading;

  function currentProviderModels() {
    const entry = providers.find((item) => item.provider === provider);
    return entry?.models || [];
  }

  function handleProviderChange(event) {
    const next = event.target.value;
    setProvider(next);
    const entry = providers.find((item) => item.provider === next);
    setModelName(entry?.models?.[0]?.id || "");
  }

  async function sendMessage(rawText) {
    const content = rawText.trim();

    if (!content || isLoading) {
      return;
    }

    const userMessage = {
      id: createId(),
      role: "user",
      content,
    };

    const nextMessages = [...messages, userMessage];
    {/*复制旧的全部，再加一个新东西在最后 */}
    setMessages(nextMessages);
    setInput("");
    setIsLoading(true);

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          messages: nextMessages.map((message) => ({
            role: message.role,
            content: message.content,
          })),
          provider,
          model_name: modelName,
          system_prompt: systemPrompt,      // ← 新增
        }),
      });

      if (!response.ok) {
        throw new Error(`请求失败，状态码：${response.status}`);
      }

      const data = await response.json();
      const assistantMessage = {
        id: createId(),
        role: "assistant",
        content: data.reply,
      };

      setMessages((currentMessages) => [...currentMessages, assistantMessage]);
    } catch (error) {
      const assistantMessage = {
        id: createId(),
        role: "assistant",
        content: `请求失败：${error.message}`,
      };

      setMessages((currentMessages) => [...currentMessages, assistantMessage]);
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

  function clearMessages() {
    setMessages([]);
  }

  return (
    <main className="page">
      <section className="card">
        <header className="header">
          <h1>AI 聊天助手</h1>
          <div className="model-selector">
            <select
              value={provider}
              onChange={handleProviderChange}
              disabled={isLoading}
              title="选择供应商"
            >
              {providers.map((item) => (
                <option key={item.provider} value={item.provider}>
                  {item.display_name || item.provider}
                </option>
              ))}
            </select>
            <select
              value={modelName}
              onChange={(event) => setModelName(event.target.value)}
              disabled={isLoading}
              title="选择模型"
            >
              {currentProviderModels().map((model) => (
                <option key={model.id} value={model.id}>
                  {model.name || model.id}
                </option>
              ))}
            </select>
          </div>
          <button
            type="button"
            className="secondary-button"
            onClick={() => setIsSystemPanelOpen((open) => !open)}
          >
            {isSystemPanelOpen ? "收起系统提示词" : "系统提示词"}
          </button>
          <button type="button" className="secondary-button" onClick={clearMessages}>
            清空对话
          </button>
        </header>

        {isSystemPanelOpen ? (
          <section className="system-panel">
            <div className="system-panel-title">
              <span>系统提示词（System Prompt）</span>
              <button
                type="button"
                className="link-button"
                onClick={() => setSystemPrompt(defaultSystemPrompt)}
                disabled={!defaultSystemPrompt || systemPrompt === defaultSystemPrompt}
              >
                恢复默认
              </button>
            </div>
            <textarea
              value={systemPrompt}
              onChange={(event) => setSystemPrompt(event.target.value)}
              rows={6}
            />
            <div className="system-panel-hint">
              修改后对下一次发送生效，不影响已有对话记录。留空则这一轮不发送系统提示词。
            </div>
          </section>
        ) : null}
        {/* 「恢复默认」按钮放在面板内部，所以只有展开时才看得到，收起时不占地方
              两份 state 的分工：
                systemPrompt        —— 当前值，用户随便改
                defaultSystemPrompt —— 默认值的副本，只在 useEffect 里写一次，之后永不改动
              一改就丢的东西必须提前存副本，否则「恢复」时没有东西可恢复。

            onClick={() => setSystemPrompt(defaultSystemPrompt)}
              必须用 () => ... 包一层，表示“点了才调用”。
              写成 onClick={setSystemPrompt(defaultSystemPrompt)} 是“渲染时立刻调用”，
              setState 又会触发重新渲染 → 再次立刻调用 → 无限循环，页面直接卡死。
              这是 React 最经典的错误之一。

            disabled={!defaultSystemPrompt || systemPrompt === defaultSystemPrompt}
              两种情况让按钮变灰：
                !defaultSystemPrompt                  副本是空的（接口挂了），没东西可恢复
                systemPrompt === defaultSystemPrompt  当前值和默认值一样，点了也没变化
              让按钮自己告诉用户“现在点没用”，比点了没反应体验好。

            原来的 placeholder="留空则使用后端默认提示词" 删掉了，因为：
              placeholder 是浏览器画的灰字提示，不是真实内容，永远不会被提交给后端；
              而 useEffect 里的 setSystemPrompt(data.default_system_prompt) 已经把
              默认提示词写进 value 了，框里有真内容时 placeholder 本来就不显示。
              它只会在“首屏 fetch 还没返回的那一瞬间”闪一下，属于死代码。

            清空输入框 = 这一轮不发 system 消息（不是回落到默认提示词）。
              前端这里始终会把 system_prompt 这个字段发出去，清空时发的是 ""，
              后端 normalize_messages 用 is None / .strip() 区分两种情况：
                字段缺失（None）→ 用 DEFAULT_SYSTEM_PROMPT
                字段是 ""      → 一条 system 都不加
              想回到默认提示词，点右上角的「恢复默认」按钮。

            标题原来是纯文本 <div>系统提示词</div>，现在用 <span> 包起来了，
              因为要在右边并排放按钮，两个并排的东西必须各自是一个元素，
              CSS 才能用 flex 的 justify-content: space-between 把它们推到两端。
        */}

        <section className="chat-box">
          {messages.map((message) => (
            <div
              key={message.id}
              className={message.role === "user" ? "message-row user-row" : "message-row"}
            >
              <div className="message-role">
                {message.role === "user" ? "我" : "助手"}
              </div>
              <div className={message.role === "user" ? "bubble user-bubble" : "bubble"}>
                {message.content}
              </div>
            </div>
          ))}
          {/* .map() 是 JavaScript 数组方法
                const numbers = [1, 2, 3];
                const doubled = numbers.map(n => n * 2);
                // doubled = [2, 4, 6] 回调函数每次返回一个 JSX 元素（<div>...</div>）
                .map() 最终返回一个由这些 JSX 元素组成的新数组；React 拿到这个数组，按顺序把每个元素渲染到屏幕上。
              key 的作用：React 要求给每个元素加一个 key 属性（除非列表是静态的且永不重排）
                对于列表稳定且唯一的key而言，React能清楚知道哪个元素是新增的，哪个元素被删除了，
                哪个元素仅仅移动了位置， React 可以最小化 DOM 操作：只增删或移动必要的节点，其余保持不动。
                其中message.id由
                function createId() {
                  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
                } 生成，结合时间戳和随机字符
          */}

          {isLoading ? (
            <div className="message-row">
              <div className="message-role">助手</div>
              <div className="bubble">正在思考...</div>
            </div>
          ) : null}
          {/* 条件渲染 {条件 ? 要显示的 : null}
            isLoading 为真 → 显示这段；否则 → null（什么都不显示）。
          */}


        </section>

        <form className="composer" onSubmit={handleSubmit}>
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={handleKeyDown}
            rows={3}
            placeholder="按 Enter 发送，Shift + Enter 换行。"
          />
          {/* 受控输入框：value + onChange 必须成对
                value={input} —— 框里显示什么，由 state 说了算
                onChange={...} —— 用户敲键盘时，把新内容写回,只写 value 不写 onChange 会得到一个打不进字的死框
                state event.target.value = "触发事件的那个 DOM 元素当前的值"
                (event) => ... 是箭头函数，等价于 function (event) { ... }
              onKeyDown={handleKeyDown} —— 交互逻辑（事件监听）
                React 对原生 DOM 事件 keydown 的封装。只要用户在键盘上按下任何键，这个函数就会被触发
                function handleKeyDown(event) {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();   // 阻止按下回车时换行
                    sendMessage(input);       // 直接发送消息
                  }
                }
          */}
          <button type="submit" className="primary-button" disabled={!canSend}>
            {isLoading ? "发送中..." : "发送"}
          </button>
        </form>
      </section>
    </main>
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);

/*
document.getElementById("root") 在 index.html 里找到 <div id="root"></div> 这个 DOM 节点，它就像一张空白画布，等着 React 去绘制。

root.render(<App />) 执行后，<div id="root"> 内部的内容会被 <App /> 返回的 JSX 结构完全替换。

<App /> 这种写法表示调用 App 这个组件。
JSX 必须只有一个根元素，所以整个界面被 <main className="page"> 包裹。
后续新增的面板，都应加在这个 <main> 内部。
*/