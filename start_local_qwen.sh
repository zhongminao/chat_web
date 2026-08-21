#!/usr/bin/env bash
# 启动本地 Qwen3.5-2B 模型服务（vLLM，OpenAI 兼容 API）
#
# 用法：
#   conda activate py313
#   bash start_local_qwen.sh          # 默认端口 8001
#   bash start_local_qwen.sh 8002     # 指定端口
#
# 启动后验证：
#   curl http://127.0.0.1:8001/v1/models
#
# 对话模板说明：模型目录里自带 chat_template.jinja（Qwen3.5 官方模板，
# 已内嵌在 tokenizer_config.json），vLLM 自动加载，无需额外指定；
# 若要自定义模板，追加 --chat-template /path/to/your_template.jinja
set -euo pipefail

# Blackwell (SM 12.x) 显卡上 FlashInfer 的 JIT 架构检测失败，
# 会报 "FlashInfer requires GPUs with sm75 or higher"；
# 关闭其采样器，改用 vLLM 原生 PyTorch 采样路径。
export VLLM_USE_FLASHINFER_SAMPLER=0

MODEL_DIR="/home/zhong/mydisk/IM_Opt/LLM/qwen3p5_2b"
PORT="${1:-8001}"
MODEL_NAME="${LOCAL_QWEN_MODEL_NAME:-local_qwen}"

echo "==> 启动 vLLM 服务"
echo "    模型目录 : ${MODEL_DIR}"
echo "    端口     : ${PORT}"
echo "    模型名   : ${MODEL_NAME}"
echo "    API 地址 : http://127.0.0.1:${PORT}/v1"
echo ""

exec vllm serve "${MODEL_DIR}" \
  --served-model-name "${MODEL_NAME}" \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.9
