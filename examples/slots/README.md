# slots

**내용을 호출자가 채우는 레이아웃 컴포넌트**

## 무엇을 보여주나

- `_slots`로 어떤 자리를 받을지 선언 (필수 여부 포함)
- `{% component_block %}` + `{% fill %}` + `{% render_slot %}`
- 카드·리스트·모달·알럿 네 가지 형태

## 돌려보기

페이지가 없는 컴포넌트 모음이다. 다른 템플릿에서 `{% component_block %}`으로 불러 쓴다.

```bash
make test ARGS="-k slots"
```

테스트는 `examples/slots/tests.py`. CI가 `make test`로 매번 돌리므로 이 예제는 조용히 낡지 않는다.

## 더 읽기

- 기능 문서: [docs/features/slots.md](../../docs/features/slots.md)
- 핵심 파일: components.py · templates/slots/
