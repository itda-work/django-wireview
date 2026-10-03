# 문서 사이트 묶음 계약

> 릴리스 자산 `docs-site-v<버전>.tar.gz`가 무엇을 약속하는지 적은 정본이다. 받는 쪽은 itda.work를 운영하는
> website 저장소(`itda-skills/website`)다. 그 배포 recipe가 아래 항목 중 여럿을 검사하고, 어긋나면 배포를 멈춘다.
> **이 계약을 바꾸려면 website 저장소에 이슈를 먼저 연다.** 묶음은 운영 중이다.

묶음을 만드는 코드는 `scripts/docs_site/build.py`(빌드)와 `scripts/docs_site/bundle.py`(묶기)이고, 릴리스는
`.github/workflows/release.yml`의 `docs` 잡이 만든다. 받는 쪽의 서빙·배포 절차는 website의 `docs/RUNBOOK.md`
§18과 website [#221](https://github.com/itda-skills/website/issues/221)에 있다.

## 1. 계약

| 항목 | 내용 | website가 검사하는가 | 이 저장소의 테스트 (`tests/test_docs_site_build.py`) |
|------|------|------|------|
| 최상위 | 항목은 전부 `wireview/` 아래다. 일반 파일과 디렉터리뿐이고(링크·장치 없음), 절대 경로나 `..`이 없다 | 예 — 풀기 전 | `test_the_bundle_holds_what_itda_work_checks` |
| `wireview/VERSION` | 릴리스 태그(`v1.1.0`) 한 줄. 파일 이름의 태그와 같다 | 예 — 풀기 전, 그리고 배포 뒤 공개 URL | `test_the_bundle_holds_what_itda_work_checks`, `test_the_version_file_is_the_tag` |
| 있어야 하는 파일 | `wireview/index.html`, `wireview/llms.txt`, `wireview/sitemap.xml` | 예 — 풀기 전 | `test_the_bundle_holds_what_itda_work_checks` |
| 경로 = URL | 묶음 안 경로가 `https://itda.work/` 아래 URL과 1:1이다. 접두사를 떼거나 붙이지 않는다 | 서빙이 그렇게 한다 | `test_the_site_root_is_the_base_path` |
| 디렉터리 URL | 페이지는 `<경로>/index.html`이고, 같은 자리에 그 문서의 Markdown `index.md`가 있다 | 배포 뒤 `/wireview/tutorial/getting-started/index.md`를 열어 본다 | `test_every_page_has_its_html_and_its_markdown` |
| `.gz` 짝 | 텍스트 파일(`.html`·`.md`·`.css`·`.js`·`.xml`·`.txt`)마다 같은 내용의 `<이름>.gz`가 있다. 서버는 즉석 압축하지 않고 이것을 보낸다 | 서빙이 기댄다 | `test_every_text_file_has_its_gzip_twin` |
| 해시 자산 | css·js는 `wireview/assets/<이름>.<16진 10자>.<css\|js>`다. 내용이 바뀌면 이름이 바뀐다. website는 `^/wireview/assets/.+\.[0-9a-f]{8,}\.(?:css\|js)$`에 맞는 것만 `immutable`로 1년 캐시한다 | 캐시 규칙이 기댄다 | `test_assets_are_named_by_their_content`, `test_the_css_and_js_are_what_itda_work_caches_for_good` |
| 이미지 | 문서가 보여 주는 저장소 이미지(`overview.jpg`)도 `wireview/assets/<이름>.<16진 10자>.<확장자>`로 묶음 안에 있다. website는 css·js와 같은 규칙(`assets/*.<16진 8자 이상>.<css·js·jpg·jpeg·png·gif·svg·webp·avif>`)으로 1년 `immutable` 캐시한다(website #221, 1.2.0부터) | 캐시 규칙이 기댄다 | `test_the_readmes_image_is_in_the_build`, `test_the_documents_images_are_copied_into_the_build_once` |
| 외부 출처 | 페이지가 스스로 불러오는 것(`src`, 스타일시트) 중 다른 출처는 Pretendard(jsDelivr) 하나뿐이다. website의 CSP가 `'self'`와 jsDelivr만 허용한다 | CSP가 강제한다 | `test_the_pages_load_nothing_from_elsewhere_but_pretendard` |
| 공개 URL | `docs/site-urls.txt`의 URL은 사라지지 않는다. 옮긴 페이지는 `docs/redirects.toml`로 옛 주소가 남는다 | 아니다 | 빌드 관문(`check_urls`), `test_a_vanished_url_fails_until_it_redirects` |
| 재현성 | 같은 커밋은 같은 바이트로 묶인다. 항목은 이름순, 소유자 0·이름 없음, 파일 0644·디렉터리 0755, gzip 헤더의 mtime 0·파일 이름 없음 | 아니다 (attestation으로 출처를 본다) | `test_the_same_commit_bundles_the_same_bytes`, `test_the_bundle_records_nothing_of_the_machine` |
| 항목의 mtime | `SOURCE_DATE_EPOCH`, 없으면 빌드한 커밋의 커밋 시각(`git log -1 --format=%ct`). 둘 다 없으면 묶지 않는다 | 아니다 — 풀 때 Release 게시 시각으로 덮어쓴다 | `test_the_bundle_records_nothing_of_the_machine`, `test_source_date_epoch_sets_the_members_time`, `test_without_git_the_bundle_asks_for_source_date_epoch` |

## 2. 서빙하는 쪽에 기대는 것

묶음이 아니라 서버 설정의 몫이라 이 저장소의 테스트로는 지킬 수 없다. website가 맞춰 두었다.

- 텍스트 파일의 `Content-Type`에 `charset=utf-8`이 붙는다(`.md`는 `text/markdown; charset=utf-8`, `VERSION`은
  `text/plain; charset=utf-8`). 없으면 llms.txt와 Markdown 원문의 한국어가 깨진다(#165). `make docs-serve`가 같은
  표를 쓴다(`scripts/docs_site/serve.py`의 `TEXT_TYPES`).
- `/wireview/<경로>`는 `/wireview/<경로>/`로 영구 이동(308)한다.

## 3. 왜 mtime이 0이 아닌가

v1.1.0 묶음은 모든 항목이 epoch 0이었다. Caddy(Go의 `ServeContent`)는 0을 "모름"으로 보고 `Last-Modified`도
`ETag`도 내지 않아, `no-cache`인 파일이 재검증 없이 매번 통째로 다시 내려갔다. website는 풀 때 Release 게시 시각으로
덮어써 해결했으므로 itda.work에는 영향이 없다. 커밋 시각은 그런 처리 없이 묶음을 그대로 푸는 다른 소비자를 위한
것이고, 같은 커밋에서 늘 같은 값이라 재현성을 해치지 않는다(#166).

## 4. 바꿀 때

1. website 저장소에 이슈를 열어 무엇이 바뀌는지 알린다. 특히 최상위 디렉터리, 있어야 하는 파일, `VERSION`의 형식,
   해시 자산 이름(캐시 규칙), 외부 출처(CSP)는 website가 검사하거나 기대는 것이다.
2. 이 문서의 표와 테스트를 같은 커밋에서 고친다.
3. website가 준비된 뒤에 릴리스한다.
