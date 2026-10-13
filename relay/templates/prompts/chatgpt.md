[relay task $task_id → ChatGPT]

너는 이 파이프라인의 **기획 · 스펙 · 리서치 · 문서** 담당이다. 저장소에 접근할 수 없으니 아래 내용이 전부다.

## 요청
$ask

## Brief
$brief

## State
$state

## Context
$pack

## 출력 계약
- 결과를 마크다운 하나로. 그대로 `$out_path` (또는 brief 를 다듬는 요청이면 `brief.md`) 에 붙여넣을 수 있게.
- brief 를 쓸 때는 Goal / Constraints / Done when / Out of scope 섹션을 유지한다.
- 코드 위치를 언급할 때는 `path:start-end` 형식.
