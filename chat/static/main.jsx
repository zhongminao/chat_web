const { useState, useEffect, useRef } = React;
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

/* 温度可选的档位。0 最稳定（同样的问题每次答案基本一样），越大越发散。
   注意：这只决定「请求里带什么值」，并不保证每个模型都听 —— 推理类模型
   （gpt-5.x / 6.x 那一挂）经常忽略这个参数，那是网关侧的行为，前端看不出来。
   上限取 1：OpenAI 系模型的 temperature 合法区间是 0~2，但推理模型往往只认
   默认值，取到 1 已经够用，不至于踩到被拒的区间。 */
const TEMPERATURE_CHOICES = [0, 0.2, 0.5, 0.7, 1];




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
  const [protocol, setProtocol] = useState([]);   // 协议历史：发给后端的真实消息（含 tool 回放）
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
  /* ---- 模型 chip + 弹出菜单（新增）----
     temperature：null 表示「没手动设过」，这时请求不带这个值，后端会用它在该
       模型上声明的默认温度（providers.yaml 里的 temperature）。
       用户一旦在菜单里选了档位，就变成具体数字并一直带着走。 */
  const [temperature, setTemperature] = useState(null);
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [menuPane, setMenuPane] = useState("root");   // root=一级菜单 | model=模型列表 | temp=温度列表
  /* useRef：拿一个「不触发重新渲染」的引用。这里要它来指菜单的 DOM 节点，
     好判断点击是不是发生在菜单外面（点外面要关掉菜单）。
     用 useState 存 DOM 节点也行，但每次赋值都会多渲染一次，纯属浪费。 */
  const modelMenuRef = useRef(null);
  /* 正文输入框的 DOM 引用。用途：菜单里的某个选项被点掉之后，那个按钮就从 DOM
     里消失了，浏览器的焦点会掉回 <body> —— 表现就是外框的蓝色「啪」一下掉回
     灰色。选完把焦点接回输入框，焦点就一直留在卡片内部，外框保持蓝色。
     接回的目标特意是**正文输入框**而不是 chip 自己：选完模型的下一步就是打字，
     焦点落在输入框里省一次点击。（DSH 那种把焦点还给触发按钮的做法更适合
     普通表单，对聊天框不合适。）
     注：这里曾经还有一个 modelChipRef（指 chip 自己），改用这个方案后它就只挂
     在 DOM 上没人读了，属于死代码，已删。 */
  const composerInputRef = useRef(null);
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
        setToolSystemPrompt(data.tool_system_prompt || "");   // 工具说明文本（来自后端）
        setBaseSnapshot(data.default_system_prompt || "");    // 非工具模式的提示词快照
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
  /* ---- 弹出菜单的关闭行为（新增）----
     原生 <select> 这两件事是白送的，换成自绘按钮就得自己写，这是这次改动的主要代价。
       点菜单外面 → 关掉并退回到一级菜单
       按 Esc     → 在二级菜单里先退回一级，已经在一级了才真的关掉
     （手机上没有 Esc 键，所以菜单里另外还放了一个「返回」按钮，见下面的 JSX。）
     事件挂在 document 上而不是菜单自己身上：点击可能落在页面任何地方，
     只有挂在 document 才能知道「点到的是外面」。 */
  useEffect(() => {
    if (!isMenuOpen) {
      return undefined;                  // 菜单没开就不用监听，顺手省掉两个监听器
    }

    function handlePointerDown(event) {
      if (modelMenuRef.current && !modelMenuRef.current.contains(event.target)) {
        setIsMenuOpen(false);
        setMenuPane("root");
      }
    }

    function handleKeyDown(event) {
      if (event.key !== "Escape") {
        return;
      }
      if (menuPane === "root") {
        setIsMenuOpen(false);
      } else {
        setMenuPane("root");
      }
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    /* 清理函数：effect 重新执行或组件卸载前，把监听器摘掉。
       不摘的话每开关一次菜单就多挂一层，久了会重复触发。 */
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isMenuOpen, menuPane]);

  const canSend = input.trim() && !isLoading;

  /* 当前选中的模型对象：从 providers 目录里按 provider + id 查出来。
     原来这里是 currentProviderModels()（给第二个 <select> 填选项用），
     换成 chip + 菜单之后不再需要那个函数了。 */
  function currentModel() {
    const entry = providers.find((item) => item.provider === provider);
    return (entry?.models || []).find((model) => model.id === modelName) || null;
  }

  /* 显示用温度 vs 实际发送的温度，这两个是分开的：
       shownTemperature —— chip 和菜单上给人看的，没手动设过就显示该模型的默认值
       temperature      —— 真正塞进请求体的，null 就让后端按模型默认值处理
     分开的好处：切换模型时显示的默认温度会自动跟着新模型走，
     而不是把上一个模型上设的温度悄悄带过去。 */
  const modelDefaultTemperature = currentModel()?.temperature ?? 0.2;
  const shownTemperature = temperature ?? modelDefaultTemperature;

  /* 选中一个模型 = 同时选定它的供应商（菜单是按供应商分区的，见下面的 JSX）。
     所以这里要一起写 provider 和 modelName 两个状态。 */
  /* 选完之后把焦点送回正文输入框：
     1) 焦点不出输入卡片，外框的蓝色不会掉回灰色（见 styles.css 里 .composer）；
     2) 下一步就能直接打字，不用再点一次。 */
  function focusComposer() {
    composerInputRef.current?.focus();
  }

  function selectModel(nextProvider, nextModelId) {
    setProvider(nextProvider);
    setModelName(nextModelId);
    setIsMenuOpen(false);
    setMenuPane("root");
    focusComposer();
  }

  function selectTemperature(next) {
    setTemperature(next);
    setIsMenuOpen(false);
    setMenuPane("root");
    focusComposer();
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
    const userProtocol = { role: "user", content };   // 协议历史里只存纯对话消息

    const nextMessages = [...messages, userMessage];
    {/*复制旧的全部，再加一个新东西在最后 */}
    setMessages(nextMessages);
    setProtocol((p) => [...p, userProtocol]);
    setInput("");
    setIsLoading(true);

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          // 完整协议回放：protocol 里保存了历史所有真实消息，
          // 包括 assistant(tool_calls) + tool 结果的成对消息；
          // 后端原样透传给模型 → 模型跨轮能看到上轮完整的工具过程。
          messages: protocol.concat(userProtocol),
          provider,
          model_name: modelName,
          system_prompt: systemPrompt,      // ← 新增
          tools_enabled: toolsEnabled,      // ← 新增：是否附带工具并允许模型调用
          // 新增：采样温度。state 是 null 时这里会发 null，后端 ChatRequest 的
          // temperature 正好是 float | None，收到 None 就回落去读 providers.yaml
          // 里该模型声明的默认温度 —— 所以「没手动设过」不需要前端自己算默认值。
          temperature: temperature,
        }),
      });

      if (!response.ok) {
        throw new Error(`请求失败，状态码：${response.status}`);
      }

      const data = await response.json();
      // 先插工具执行步骤（可展开查看），再插最终回复
      const newItems = [];
      (data.steps || []).forEach((step) => {
        newItems.push({
          id: createId(),
          role: "tool-step",
          tool: step.tool,
          result: step.result,
          ok: step.ok,
        });
      });
      const assistantMessage = {
        id: createId(),
        role: "assistant",
        content: data.reply,
      };
      newItems.push(assistantMessage);

      setMessages((currentMessages) => [...currentMessages, ...newItems]);
      // 协议历史追加本次运行产生的完整协议消息（trace），下一轮原样回放
      if (data.trace && data.trace.length) {
        setProtocol((p) => [...p, ...data.trace]);
      }
    } catch (error) {
      const assistantMessage = {
        id: createId(),
        role: "assistant",
        content: `请求失败：${error.message}`,
      };

      // 失败轮不污染协议历史：错误气泡只进展示，不回放给模型
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
    setProtocol([]);   // 清空对话 = 清空展示 + 清空协议历史
  }

  // 工具模式开关：勾选时把"工具说明 + 当前提示词"拼进面板（看得见的拼接，不是后端黑盒），
  // 取消时还原成勾选前的提示词。拼好的全文随请求原样发送，后端不再自动拼接。
  function handleToolsToggle(event) {
    const next = event.target.checked;
    if (next) {
      setBaseSnapshot(systemPrompt || defaultSystemPrompt);   // 记住开之前的提示词
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

  return (
    <main className="page">
      <section className="card">
        <header className="header">
          <h1>AI 聊天助手</h1>
          {/* 原来这里有两个 <select>（供应商 + 模型）。现在合并成输入框下方
              发送按钮左边的一个 chip，点开是「模型 / 温度」二级菜单 —— 见
              <form className="composer"> 里的 .model-menu。 */}
          <button
            type="button"
            className="secondary-button"
            onClick={() => setIsSystemPanelOpen((open) => !open)}
          >
            {isSystemPanelOpen ? "收起系统提示词" : "系统提示词"}
          </button>
          <label className="tool-toggle" title="勾选后模型可调用 read/write/edit/bash 工具，面板会自动拼入工具说明">
            <input
              type="checkbox"
              checked={toolsEnabled}
              onChange={handleToolsToggle}
              disabled={isLoading}
            />
            工具模式
          </label>
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
                onClick={() => {
                  setBaseSnapshot(defaultSystemPrompt);
                  setSystemPrompt(
                    toolsEnabled && toolSystemPrompt
                      ? toolSystemPrompt + "\n\n" + defaultSystemPrompt
                      : defaultSystemPrompt
                  );
                }}
                disabled={
                  !defaultSystemPrompt ||
                  systemPrompt ===
                    (toolsEnabled && toolSystemPrompt
                      ? toolSystemPrompt + "\n\n" + defaultSystemPrompt
                      : defaultSystemPrompt)
                }
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
                {message.role === "user" ? "我：" : "助手："}
              </div>
              <div className={message.role === "user" ? "bubble user-bubble" : "bubble"}>
                {message.role === "tool-step" ? (
                  <details className="tool-step">
                    <summary>
                      ⚙ {message.tool} {message.ok ? "" : "（执行失败）"}
                    </summary>
                    <pre>{message.result}</pre>
                  </details>
                ) : (
                  message.content
                )}
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
              <div className="message-role">助手：</div>
              {/* 新增的 loading-bubble 类：等待态在 DSH 那边是一行「扫光文字」
                  （ChatView 的 turnStatus），不是一块静态气泡。文字内容没变，
                  只是多挂一个类名，样式写在 styles.css 里。 */}
              <div className="bubble loading-bubble">正在思考...</div>
            </div>
          ) : null}
          {/* 条件渲染 {条件 ? 要显示的 : null}
            isLoading 为真 → 显示这段；否则 → null（什么都不显示）。
          */}


        </section>

        {/* 菜单展开时给表单多挂一个 is-menu-open 类，让输入卡片的外框在整个
            选模型的过程中保持蓝色不变 —— 详见 styles.css 里 .composer 那段。
            只靠 CSS 的 :focus-within 判断不稳，所以把状态显式写在类名上。 */}
        <form
          className={isMenuOpen ? "composer is-menu-open" : "composer"}
          onSubmit={handleSubmit}
        >
          <textarea
            ref={composerInputRef}
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
          {/* 新增的 .composer-actions 包裹层：DSH 的输入卡片是「上面 textarea，
              下面一排操作按钮」，主按钮落在这一排的右下角。
              原来发送按钮是靠 position:absolute 贴在卡片右下角的，textarea 再用
              padding-right:151px 手工躲开它 —— 那种写法改一下字号或按钮文案就会
              错位，所以换成正常的上下两行布局。 */}
          <div className="composer-actions">
            {/* ---- 模型 chip + 二级弹出菜单（新增）----
                位置对齐 DSH：和发送按钮同属操作行的右侧簇，chip 在前、按钮在后
                （上游 InputBar.module.css 的注释：model + send on the right）。

                ref={modelMenuRef} 挂在最外层这个 div 上，而不是只挂菜单本身：
                chip 和菜单必须算作「同一块内部区域」，否则点 chip 会被上面那个
                document 上的 mousedown 监听当成「点到了外面」，菜单刚开就被关掉。 */}
            <div className="model-menu" ref={modelMenuRef}>
              <button
                type="button"
                className="model-chip"
                onClick={() => {
                  setIsMenuOpen((open) => !open);
                  setMenuPane("root");          // 每次重新打开都从一级菜单开始
                }}
                disabled={isLoading}
                aria-haspopup="menu"
                aria-expanded={isMenuOpen}
                title="选择模型与温度"
              >
                <span className="model-chip-name">
                  {currentModel()?.name || modelName || "选择模型"}
                </span>
                {/* 温度用更浅的第三级文字色跟在名字后面 —— 对应上游 chip 上
                    那个跟在模型名后面的 effort 值（triggerEffort）。 */}
                <span className="model-chip-temp">{shownTemperature}</span>
                <span className="model-chip-chevron" aria-hidden="true" />
              </button>

              {isMenuOpen ? (
                /* 菜单向上弹：它在输入卡片的底部，往下弹会跑出屏幕。
                   这个 bottom: 100% 的写法照抄上游 ModelSelect.module.css 第 67 行。 */
                <div className="model-popup" role="menu">
                  {menuPane === "root" ? (
                    /* 一级菜单：两格，各自右边显示当前值 + 一个「›」表示还能往里点。
                       规格照抄上游 Menu_cell（ModelSelect.module.css 的 .cell）：
                       40px 行高、10px 左右内边距、10px 圆角。 */
                    <div className="model-pane">
                      <button
                        type="button"
                        role="menuitem"
                        className="model-cell"
                        onClick={() => setMenuPane("model")}
                      >
                        <span className="model-cell-label">模型</span>
                        <span className="model-cell-value">
                          {currentModel()?.name || modelName}
                        </span>
                        <span className="model-cell-chevron" aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        role="menuitem"
                        className="model-cell"
                        onClick={() => setMenuPane("temp")}
                      >
                        <span className="model-cell-label">温度</span>
                        <span className="model-cell-value">{shownTemperature}</span>
                        <span className="model-cell-chevron" aria-hidden="true" />
                      </button>
                    </div>
                  ) : null}

                  {menuPane === "model" ? (
                    /* 二级菜单 · 模型：按供应商分区。providers 这个数组本来就是
                       [{provider, display_name, models: [...]}, ...] 的形状，
                       和这里要的两层结构一模一样，所以不用改后端一个字。 */
                    <div className="model-pane">
                      {/* 返回上一级。手机上没有 Esc 键，少了这个按钮就出不去。 */}
                      <button
                        type="button"
                        className="model-back"
                        onClick={() => setMenuPane("root")}
                      >
                        <span className="model-back-arrow" aria-hidden="true" />
                        返回
                      </button>
                      {providers.map((item) => (
                        <section key={item.provider} className="model-group">
                          {/* 分区标题：供应商显示名，滚动时粘在顶部 */}
                          <div className="model-group-title">
                            {item.display_name || item.provider}
                          </div>
                          {(item.models || []).map((model) => {
                            const selected =
                              item.provider === provider && model.id === modelName;
                            return (
                              <button
                                key={model.id}
                                type="button"
                                role="menuitemradio"
                                aria-checked={selected}
                                className={
                                  selected ? "model-option is-selected" : "model-option"
                                }
                                onClick={() => selectModel(item.provider, model.id)}
                              >
                                <span className="model-option-name">
                                  {model.name || model.id}
                                </span>
                                {/* 选中标记是右侧的对勾，不是给整行填色 —— 上游就是这个做法 */}
                                {selected ? (
                                  <span className="model-option-check" aria-hidden="true">
                                    ✓
                                  </span>
                                ) : null}
                              </button>
                            );
                          })}
                        </section>
                      ))}
                    </div>
                  ) : null}

                  {menuPane === "temp" ? (
                    /* 二级菜单 · 温度 */
                    <div className="model-pane">
                      <button
                        type="button"
                        className="model-back"
                        onClick={() => setMenuPane("root")}
                      >
                        <span className="model-back-arrow" aria-hidden="true" />
                        返回
                      </button>
                      <div className="model-group-title">温度</div>
                      {TEMPERATURE_CHOICES.map((value) => {
                        const selected = value === shownTemperature;
                        return (
                          <button
                            key={value}
                            type="button"
                            role="menuitemradio"
                            aria-checked={selected}
                            className={
                              selected ? "model-option is-selected" : "model-option"
                            }
                            onClick={() => selectTemperature(value)}
                          >
                            <span className="model-option-name">
                              {value}
                              {/* 标出该模型的默认值，换模型时好对照 */}
                              {value === modelDefaultTemperature ? "（默认）" : ""}
                            </span>
                            {selected ? (
                              <span className="model-option-check" aria-hidden="true">
                                ✓
                              </span>
                            ) : null}
                          </button>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>

            <button type="submit" className="primary-button" disabled={!canSend}>
              {isLoading ? "发送中..." : "发送"}
            </button>
          </div>
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