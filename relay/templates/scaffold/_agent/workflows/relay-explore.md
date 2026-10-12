---
description: relay task 탐색 리포트(explore.md) 작성
---
1. 사용자에게 task id 를 확인한다 (없으면 `.relay/tasks/` 의 가장 최근 폴더).
2. `brief.md` 를 읽는다.
3. 관련 파일을 탐색하고 `.agent/rules/relay.md` 의 형식대로 `explore.md` 를 작성한다.
4. 마지막에 다음 명령을 안내한다: `relay pack <id> --refs explore.md`
