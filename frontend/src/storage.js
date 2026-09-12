// localStorage 读写。存储不可用时（隐私模式 / file:// / 浏览器禁用）直接访问会抛
// SecurityError —— 而这些读取可能发生在 useState 的初始化里，抛出去就是整页空白。
// 所以统一走这里：读不到当没有，写不进就只活在内存里。

export function readStored(key) {
  try {
    return window.localStorage.getItem(key);
  } catch (error) {
    return null;
  }
}

export function writeStored(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch (error) {
    /* 存不下就算了 */
  }
}
