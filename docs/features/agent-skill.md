# 에이전트 스킬 — 앱 개발자용

> AI 코딩 에이전트가 wireview 앱을 제대로 짜게 만드는 배포용 스킬

---

## 개요

wireview의 함정은 대부분 **조용히** 실패한다. 이벤트 핸들러를 `async def`로 쓰지 않으면
클라이언트가 그 버튼을 누르는 순간까지 아무 신호가 없고, `{% tag_header %}`를 빠뜨린
컴포넌트는 그냥 정적인 HTML로 남는다. 에이전트에게 저장소 전체를 읽히는 대신,
이 함정들과 라우팅만 담은 스킬을 프로젝트에 깔아 둔다.

두 스킬을 구분한다.

| 스킬 | 대상 | 위치 |
|------|------|------|
| `wireview` | **wireview로 앱을 만드는 사람** | `skills/wireview/` (정본), 휠에 포함 |
| `wireview-dev` | 이 저장소에서 라이브러리 자체를 고치는 사람 | `.claude/skills/wireview-dev/` |

## 구조

```
skills/wireview/                  정본. 휠에 wireview/agent_skills/wireview/로 실린다
├── SKILL.md                      라우팅 + 컴포넌트 추가 절차 + 함정
└── references/
    ├── component.md              상태, 라이프사이클, 핸들러, 브로드캐스트, 비동기
    ├── templates.md              템플릿 태그, {% on %} 수정자, 슬롯, DOM 속성
    ├── streams-uploads.md        Streams, Presence, 파일 업로드
    └── testing.md                mount() 기반 테스트

.claude/skills/wireview -> ../../skills/wireview     저장소가 자기 스킬을 dogfood
```

**정본을 복제하지 않는다.** 스킬은 `docs/features/`와 `docs/tutorials/`를 가리키기만 한다.
복제하면 문서가 셋(코드·docs·스킬)이 되고 반드시 어긋난다.

## 설치

```bash
python manage.py wireview_agent_setup
```

`.claude/skills/wireview/`에 스킬을 복사한다.

| 옵션 | 뜻 |
|------|------|
| `--target <dir>` | 설치할 프로젝트 디렉터리 (기본: `settings.BASE_DIR`, 없으면 현재 디렉터리) |
| `--force` | 이미 있는 디렉터리를 덮어쓴다 |

이미 있는 디렉터리는 `--force` 없이는 덮어쓰지 않는다. 대상이 **심링크**면 손대지 않는다 —
이 저장소처럼 정본을 심링크로 dogfood하는 구성을 복사본으로 갈아 끼우면 정본이 조용히 갈라진다.

## Codex 등 다른 에이전트

스킬 자동 발동은 Claude Code의 기능이다. `.claude/skills/`를 읽지 않는 에이전트에게는
`AGENTS.md`에 포인터를 둔다.

```markdown
wireview로 컴포넌트를 만들기 전에 `.claude/skills/wireview/SKILL.md`를 읽어라.
```

자동은 아니지만 동작한다.

## Dogfooding

이 저장소는 그 스킬을 매일 쓰는 유일한 장소다(예제·튜토리얼·테스트를 쓸 때).
여기서 굴려 보지 않은 스킬을 사용자에게 배포하지 않는다. 그래서 `.claude/skills/wireview`는
복사본이 아니라 `skills/wireview`를 가리키는 심링크다. 스킬을 고칠 때는 `skills/wireview/`를 고친다.

가드는 두 곳이다.

- `tests/test_agent_docs.py` — 스킬 frontmatter와 참조 경로·make 타깃·GAP 번호가 실재하는지
  (심링크 덕분에 이 스킬도 자동으로 검사 대상이다)
- `tests/test_agent_skill.py` — 심링크가 정본을 가리키는지, 휠이 스킬을 싣는지,
  `wireview_agent_setup`이 실제로 설치·거부·보존하는지

## 관련 문서

- [시스템 체크](./checks.md) — 스킬이 "만들고 나면 `manage.py check`를 돌려라"로 가리키는 대상
- [타입 스텁](./type-stubs.md) — 에이전트·IDE가 컴포넌트 시그니처를 읽게 하는 다른 축
