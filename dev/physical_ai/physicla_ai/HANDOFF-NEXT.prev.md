STATUS: CONTINUE
META: 2026-11-08 03:20 · main@064e98d · uncommitted 12 files
ACTIVE: none
## Waiting
- C-022 갈래 선택(디지털/혼성 신호, 또는 C-020 결과 후 결정) | human
- FPGA 보드 유무·모델명 | human
- C-021 입력(합성/실제 데이터)과 행동 오차 합격 기준 | human
- C-020 착수 승인 | human
- C-019 착수 여부 | human
- conclusion.md H2 "부분 지지" 수정 커밋 | human
- research/codesign/ 는 사용자 지시로 커밋 안 함 | human
## Uncommitted
- hypothesis/conclusion.md
- experiments/arch_colab_troubleshooting.md, experiments/e1_colab_troubleshooting.md, experiments/f_colab_troubleshooting.md
- research/codesign/ (survey.md, papers.json, human_role.md, limits.json)
- tasks/C-010.md, tasks/C-019.md, tasks/C-020.md, tasks/C-021.md, tasks/C-022.md
- .relay/HANDOFF-NEXT.md, .relay/HANDOFF-NEXT.prev.md
## Session
- 사용자 방향: 유형 F(병목→알고리즘 튜닝→HW 설계) 트랙 추가. 핵심 구현은 사용자, AI는 설명·검토. 역할 근거는 research/codesign/human_role.md.
- 도구: ngspice, yosys, iverilog, verilator, magic, SKY130 PDK(~/.volare, PDK_ROOT 미설정). zsh 글롭 주의.
- scout.md 없음 → general-purpose+sonnet 사용. 에이전트 수치는 원문 대조 후 사용.
