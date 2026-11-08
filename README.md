# .relay

파일 기반 인수인계(relay) 설정과 상태 백업.

| 폴더 | 로컬 원본 | 범위 |
|---|---|---|
| `global/` | `~/.claude/` (CLAUDE.md, commands/handoff·pickup, skills/relay) | 모든 프로젝트 공통 |
| `dev/<경로>/` | `~/dev/<경로>/.relay/` | 그 프로젝트의 진행 상태. 폴더 이름은 출처 경로다 |

- `global/skills/relay/research/papers/`의 논문 PDF는 저작권 때문에 올리지 않는다(`research/papers.md`에 서지와 요약이 있다).
- 백업 파일(`*.bak-*`)은 올리지 않는다.
