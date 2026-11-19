# Third-party source record

This file records exact source revisions so the competition prototype remains auditable.

| Project | Local development snapshot (excluded from this repository) | Revision | Intended use | License status |
|---|---|---|---|---|
| Qwen-Agent | `third_party/qwen-agent` | `31a4d36d123688581a9e9744427272b33ce940e0` | Auditable upstream snapshot; runtime uses the pinned `qwen-agent==0.0.31` package | Apache-2.0 (`LICENSE` present) |
| MagicMakeup | `third_party/magic-makeup` | `052ccb013eb24cdf223beb6145e26fec92f94d07` | Optional makeup-transfer research and demo asset | No root license file found at download time; do not redistribute or integrate until clarified |

The core implementation does not copy source code from either repository. It imports the pinned
Qwen-Agent package for orchestration, calls the configured model service through an
OpenAI-compatible interface, and keeps MagicMakeup outside the runtime dependency graph.
# MediaPipe Face Landmarker

原开发环境中使用的 `third_party/magic-makeup/mediapipe/face_landmarker.task` 与 Google MediaPipe 官方模型一致：
https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task 。
人脸定位由 MediaPipe Tasks 执行；模型用于本地关键点与裁剪，不上传给图片服务。
