# AGENTS.md

Codex를 비롯해 `.claude/skills/`를 읽지 않는 에이전트를 위한 포인터다.
Claude Code는 같은 파일들을 스킬로 자동 로드하므로 이 문서를 읽을 필요가 없다.

## 이 저장소에서 무엇을 하려는가

| 목적 | 읽을 것 |
|---|---|
| **wireview 라이브러리 자체를 고친다** (이 저장소가 작업 대상) | `CLAUDE.md`(지도와 금지선) → `.claude/skills/wireview-dev/SKILL.md`(작업 절차) |
| **wireview로 앱을 만든다** (컴포넌트·템플릿·테스트를 쓴다) | `skills/wireview/SKILL.md` → 필요한 참조만 `skills/wireview/references/` |

`skills/wireview/`는 사용자 프로젝트에 배포되는 스킬의 정본이고, `.claude/skills/wireview`는
그것을 가리키는 심링크다. 이 저장소가 자기 스킬을 매일 쓰는 유일한 장소이므로,
스킬이 틀리면 여기서 먼저 드러난다. 고칠 때는 `skills/wireview/` 쪽을 고친다.

설치된 프로젝트에서는 `python manage.py wireview_agent_setup`이 이 스킬을
`.claude/skills/wireview/`로 복사한다.
