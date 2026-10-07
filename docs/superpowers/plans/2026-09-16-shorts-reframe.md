# Shorts reframing reliability — bounded improvement

User request: improve existing Shorts with suitable open-source technology and
verify that the rebuild button actually applies every cropping option.

Scope: retain editor/API workflow and FFmpeg. Upgrade the existing MediaPipe
adapter to Tasks using a locally bundled Apache-2.0 BlazeFace model. No new
service or cloud dependency. Existing clips, manual captions and order retained.

Evidence and completed implementation:
- Dense samples previously collapsed to a single final keyframe. A failing
  regression reproduces this; smoothing now preserves both endpoints and up to
  36 samples distributed over the complete clip. Interpolation holds the first
  position before detection instead of extrapolating.
- Narrow portrait input failed FFmpeg crop sizing. Cover scaling fixes it.
- mp.solutions is absent from modern MediaPipe; use Tasks VIDEO on CPU with
  sequential RGB frame reads and cancellation checks. Model and license bundled.
- Persist requested/resolved mode and fallback reason with each successful
  output. Serve it in output listings and show it beneath the actual player.
  Missing/invalid legacy reports cannot hide an existing file.
- Mode descriptions explain crop tradeoffs and the distinction between saved
  edits and rendered results. Safe-zone guides are explicitly preview-only.

Verification: regression tests, real FFmpeg layouts at 1080x1920 and moving
pixel checks; native MediaPipe smoke test and detection on a user screenshot;
editor interaction tests; existing Shorts/montage/analysis regressions, Vite
build and focused ESLint. No Windows or GPU end-to-end claim. Local browser
preview remains unavailable in this environment.

Upstream: https://github.com/google-ai-edge/mediapipe
https://developers.google.com/edge/mediapipe/solutions/vision/face_detector/python
Compared AutoFlip and PySceneDetect; integrating a replacement pipeline was
outside this bounded change. Licensing notice/model card included with model.
