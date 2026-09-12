"""配置自检：类型与取值范围。改这份文件让它通过是无效的 —— 判定会把它的哈希比一遍。"""
import config

assert isinstance(config.PORT, int), f"PORT 应该是 int，实际是 {type(config.PORT).__name__}"
assert 1 <= config.PORT <= 65535, f"PORT 越界: {config.PORT}"
assert isinstance(config.TIMEOUT_SECONDS, int) and config.TIMEOUT_SECONDS >= 1
assert 0 <= config.MAX_RETRIES <= 10
print("配置自检通过")
