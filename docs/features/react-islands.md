# React 섬과 함께 쓰기

> 페이지의 대부분은 wireview로 두고, 지도·차트처럼 클라이언트에서 오래 사는 부분만 React로 그린다(#224).

[섬](./islands.md)이 기본 장치다. 이 문서는 그 위에 React를 얹는 법을 다룬다: 섬을 등록하는 코드, Vite로 번들을 만들어
싣는 법, 개발 서버, CSP, 공개 캐시에 담기는 페이지, 로그인 뒤의 기능. wireview는 npm 패키지를 내지 않는다.
`window.wireview`는 `{% wireview_header %}`가 싣는 번들이 만드는 전역이고, React 번들은 그것을 쓰기만 한다.

## 서버: 컴포넌트

```python
# explorer/live_sessions.py
from wireview import live_session

# 누구나 들어온다(authorize 없음). 그래도 경계 안이라 로그아웃이 열린 소켓을 닫는다 — 아래 "로그인 뒤의 기능"
explore = live_session("explore")
```

```python
# explorer/views.py
from django.shortcuts import render

from .live_sessions import explore


@explore.view
def explorer(request):
    return render(request, "explorer/page.html")
```

```python
# explorer/live.py
from wireview import Component

from .models import SavedView


class Explorer(Component):
    class Meta:
        template_name = "explorer/explorer.html"
        live_sessions = {"explore"}

    zoom: int = 10
    saved: int = 0

    @property
    def map_props(self) -> dict:
        return {"zoom": self.zoom, "saved": self.saved, "signedIn": self.user.is_authenticated}

    async def zoom_in(self):
        self.zoom += 1

    async def handle_hook_event(self, hook_id, event, payload):
        if event == "save_view":
            if not self.user.is_authenticated:
                return {"ok": False, "reason": "login"}
            zoom = int(payload.get("zoom", self.zoom))
            await SavedView.objects.acreate(user=self.user, zoom=zoom)
            self.saved += 1
            return {"ok": True}
        return None
```

```django
{% load wireview %}

<section {% tag_header %}>
  <button type="button" {% on "click" "zoom_in" %}>확대</button>
  <span>저장한 보기 {{ this.saved }}개</span>
  {% island "Map" props=this.map_props class="map" %}
</section>
```

섬 밖의 버튼과 글자는 평소의 wireview다. 섬은 `props`가 바뀔 때마다 새 props를 받는다 — 위 버튼을 눌러도, 섬이
`save_view`를 보내도 같다. 화면의 진실은 서버의 상태 하나다.

## 클라이언트: 섬 등록

```javascript
// frontend/islands.jsx
import { createRoot } from "react-dom/client";
import { MapView } from "./MapView";
import "./islands.css";

window.wireview.islands.Map = {
  mount(el, props, ctx) {
    const root = createRoot(el);
    const render = (next) => root.render(<MapView {...next} ctx={ctx} />);
    render(props);
    return { update: render, unmount: () => root.unmount() };
  },
};
```

```javascript
// frontend/MapView.jsx
import { useEffect, useState } from "react";

export function MapView({ zoom, saved, signedIn, ctx }) {
  const [saving, setSaving] = useState(false);
  const [online, setOnline] = useState(ctx.status === "connected");

  // ctx.on과 ctx.onStatus는 떼는 함수를 돌려준다: useEffect의 정리 함수로 그대로 쓴다
  useEffect(() => ctx.on("listings_changed", ({ count }) => console.log(count)), [ctx]);
  useEffect(() => ctx.onStatus((status) => setOnline(status === "reconnected")), [ctx]);

  async function save() {
    setSaving(true);
    try {
      const reply = await ctx.push("save_view", { zoom });
      if (!reply.ok) alert("로그인이 필요합니다");
    } catch (error) {
      // error.reason: "disconnected" | "destroyed" | "error" | "gone"
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <p>확대 {zoom} · 저장 {saved}</p>
      <button disabled={saving || !online || !signedIn} onClick={save}>이 보기 저장</button>
    </div>
  );
}
```

- `ctx.push`의 Promise는 그 이벤트의 렌더가 화면에 반영되고 **새 props를 `update`로 건넨 뒤** 풀린다([#223](./hooks.md#서버로-보내기-pushevent)).
  wireview가 보장하는 것은 거기까지다 — `update`가 부른 `root.render`는 React가 그릴 일을 예약할 뿐이고, wireview는
  React의 커밋을 기다리지 않는다. 위처럼 같은 틱의 상태 변경은 보통 한 번에 그려지지만, 커밋된 DOM이 필요한 코드는
  React 쪽(`useEffect`)에서 기다린다.
- `frontend/islands.css`는 섬의 스타일이다(지도 영역의 높이 등). 진입이 import하므로 빌드가 `islands.css`로 낸다.
- 섬의 상태(`saving`, 지도의 위치)는 React의 것이고 렌더가 건드리지 않는다. 서버가 알아야 하는 것은 `ctx.push`로 보낸다.
- `ctx`는 마운트마다 하나라 `useEffect`의 의존성으로 두어도 다시 등록되지 않는다. 섬이 떠나면 그 섬이 단 구독은
  wireview가 함께 뗀다.
- props는 페이지의 HTML 속성으로 간다. 크게 보내야 하는 데이터(수천 개의 마커)는 props 대신 `ctx.push`의 응답으로 받는다.

## Vite로 번들 만들기

wireview는 Vite를 알지 않는다. 섬 번들은 앱의 정적 파일 하나다.

```javascript
// vite.config.js
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ command }) => ({
  plugins: [react()],
  // 빌드는 STATIC_URL 아래 번들이 놓일 곳: CSS의 url()과 동적 import가 이 주소로 간다.
  // 개발 서버는 루트에서 준다 — base 밖의 주소에는 404로 답한다.
  base: command === "build" ? "/static/explorer/islands/" : "/",
  build: {
    outDir: "explorer/static/explorer/islands",
    emptyOutDir: true,
    rollupOptions: {
      input: "frontend/islands.jsx",
      output: {
        // 템플릿이 {% static %}로 찾는 둘만 이름을 고정한다
        entryFileNames: "islands.js",
        assetFileNames: (asset) =>
          (asset.names ?? [asset.name]).includes("islands.css") ? "islands.css" : "assets/[name]-[hash][extname]",
        chunkFileNames: "[name]-[hash].js",
      },
    },
  },
}));
```

```django
{# base.html: 섬이 있는 페이지로 boost 이동해 들어올 수 있는 모든 페이지의 레이아웃 #}
{% load static wireview %}
<head>
  <meta property="csp-nonce" nonce="{{ csp_nonce }}">
  {% wireview_header %}
  <link rel="stylesheet" href="{% static 'explorer/islands/islands.css' %}">
  <script type="module" src="{% static 'explorer/islands/islands.js' %}" nonce="{{ csp_nonce }}"></script>
</head>
```

- **모든 페이지가 공유하는 레이아웃의 `<head>`에 싣는다.** boost 이동은 `<body>`만 바꾸고 `<head>`의 새 스크립트는
  싣지 않는다 — 섬이 없는 페이지에서 섬이 있는 페이지로 이동해 들어오면, 그 페이지의 `<head>`에만 있는 번들은
  돌지 않고 섬은 정의를 영영 기다린다. 훅 파일을 모든 페이지에 싣는 것과 같은 이유다.
- **`{% wireview_header %}` 뒤에 싣는다.** 헤더의 번들은 `defer`이고, `type="module"` 스크립트도 문서 순서대로 그 뒤에
  돈다 — 섬 코드가 돌 때 `window.wireview`가 있다.
- 템플릿이 찾는 진입 파일(`islands.js`, `islands.css`)만 이름을 고정하고 캐시 무효화는 Django의
  `ManifestStaticFilesStorage`에 맡긴다. 이미지·글꼴과 청크는 Vite의 해시 이름을 둔다 — Django의 manifest는 JS 안의
  자산 주소를 고쳐 쓰지 않으므로, 고정 이름이면 바뀐 이미지가 캐시에서 옛것으로 나간다. 청크는 상대 경로로
  서로를 부르므로 같은 디렉터리에 둔다.
- 진입이 여럿이거나 이름을 모두 해시로 두려면 Vite의 manifest를 읽는 템플릿 태그를 쓴다(django-vite 같은 패키지).
  wireview와는 무관하다.

## 개발 서버(HMR)

개발 중에는 Vite 개발 서버가 모듈을 직접 준다. Vite의 [백엔드 통합](https://vite.dev/guide/backend-integration) 그대로이고,
React 플러그인은 그 앞에 preamble 스크립트가 필요하다.

```django
<meta property="csp-nonce" nonce="{{ csp_nonce }}">
{% wireview_header %}
{% if vite_dev %}
  <script type="module" nonce="{{ csp_nonce }}">
    import RefreshRuntime from "http://localhost:5173/@react-refresh";
    RefreshRuntime.injectIntoGlobalHook(window);
    window.$RefreshReg$ = () => {};
    window.$RefreshSig$ = () => (type) => type;
    window.__vite_plugin_react_preamble_installed__ = true;
  </script>
  <script type="module" src="http://localhost:5173/@vite/client" nonce="{{ csp_nonce }}"></script>
  <script type="module" src="http://localhost:5173/frontend/islands.jsx" nonce="{{ csp_nonce }}"></script>
{% else %}
  <link rel="stylesheet" href="{% static 'explorer/islands/islands.css' %}">
  <script type="module" src="{% static 'explorer/islands/islands.js' %}" nonce="{{ csp_nonce }}"></script>
{% endif %}
```

- `vite_dev`는 프로젝트가 정하는 값이다(설정을 읽는 컨텍스트 프로세서 등). 개발 중에는 CSS를 Vite가 `<style>`로
  넣으므로 `<link>`가 없다.
- 개발 서버의 주소가 `localhost:5173`이 아니면(컨테이너, 다른 호스트) 위 주소와 CSP의 출처를 함께 바꾸고, Vite의
  `server.origin`·`server.cors`를 그에 맞춘다.
- React 컴포넌트 파일을 고치면 Fast Refresh가 그 자리에서 바꾼다 — 섬은 다시 마운트되지 않는다. 등록 파일
  (`islands.jsx`)을 고치면 Vite가 페이지를 새로 고친다.
- 템플릿을 고치면 wireview의 개발 서버 리로드(#180)가 컴포넌트를 다시 join한다. 섬은 그대로이고, 바뀐 props만 받는다.

## CSP

wireview의 바인딩과 섬의 props는 인라인 스크립트가 아니라 속성이므로 `'unsafe-inline'` 없이 돈다([CSP](./csp.md)).
섬 번들을 싣는 `<script>`에는 요청의 nonce를 붙인다. Django 6.0의 `django.middleware.csp.ContentSecurityPolicyMiddleware`가
요청에 nonce를 두고, `django.template.context_processors.csp`를 `TEMPLATES`의 `context_processors`에 더하면 템플릿에
`csp_nonce`가 생긴다. `{% wireview_header %}`는 같은 nonce를 제 태그에 붙인다.

```python
# settings.py
from django.utils.csp import CSP

SECURE_CSP = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF, CSP.NONCE],
    "style-src": [CSP.SELF, CSP.NONCE],
    "connect-src": [CSP.SELF],
}

if VITE_DEV:
    SECURE_CSP["script-src"].append("http://localhost:5173")
    SECURE_CSP["connect-src"].append("ws://localhost:5173")
```

개발 중 Vite가 CSS를 `<style>`로 넣는다. 그 태그에도 nonce를 달려고 위 예시는 `<meta property="csp-nonce" nonce="{{ csp_nonce }}">`를
Vite의 클라이언트보다 앞에 둔다 — 클라이언트가 그 값을 읽는다(Vite의 `html.cspNonce` 문서).

### 공개 캐시에 담기는 페이지

nonce는 응답마다 다르다. 캐시가 HTML만 담고 CSP 헤더는 응답마다 새로 만들면(미들웨어가 캐시 뒤에 있거나, CDN이
헤더를 따로 붙이면) 담아 둔 HTML의 nonce가 헤더와 맞지 않는다. 그러면 nonce로 허용되던 것 — 인라인 `<style>`·`<script>`,
`'strict-dynamic'` 정책에서 nonce로 믿던 스크립트 — 을 브라우저가 막는다(`'self'`가 허용하는 같은 출처의 외부
스크립트는 nonce와 무관하게 돈다). HTML과 헤더를 함께 담는 캐시는 어긋나지 않지만, 그때는 모든 방문자가 같은 nonce를
받아 nonce가 비밀이 아니게 된다. 그런 페이지는 nonce가 아닌 정책을 쓴다.

```python
WIREVIEW_HEADER_STYLE = "'sha256-9wSH+FNcf1J15W2Bp98kNXSBki4k6cyx3+rcC+BzFkU='"

SECURE_CSP = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF],
    "style-src": [CSP.SELF, WIREVIEW_HEADER_STYLE],
    "connect-src": [CSP.SELF],
}
```

- wireview가 싣는 스크립트는 모두 외부 파일(`src`)이다. 정적 파일이 같은 출처에 있으면 `'self'`로 충분하고, CDN에
  있으면 그 출처를 더한다. 섬 번들도 같다.
- 헤더의 인라인 `<style>`(연결이 끊겼을 때의 표시)은 내용이 고정이라 위 해시로 허용된다. 이 해시는 wireview 버전과
  함께 바뀔 수 있다 — 바뀌면 변경 이력에 적는다(`tests/test_islands.py`가 문서의 해시를 헤더와 맞춘다).
- 본문에 남는 `nonce` 속성은 정책에 nonce가 없으면 브라우저가 무시하므로 지우지 않아도 된다.
- 인라인 스크립트가 꼭 필요한 페이지는 nonce 정책을 쓰고 공개 캐시 대신 `private` 캐시를 쓴다.

## 로그인 뒤의 기능

섬이 `ctx.push`로 보내는 이벤트는 그 컴포넌트의 `handle_hook_event()`가 받는다. 그 연결의 사용자(`self.user`)로 돌고,
섬의 코드는 인증 정보를 다루지 않는다.

- **이벤트 이름과 payload는 클라이언트가 보낸 것이다.** `handle_hook_event()`는 아는 이름만 처리하고, payload의 값을
  검사한 뒤 쓴다(위 `int(payload.get(...))`). 권한은 매번 서버에서 확인한다.
- **props는 페이지의 HTML에 그대로 실린다.** 그 사용자에게 보여도 되는 것만 넣는다.
- **로그인 뒤의 기능이 있는 페이지는 공개 페이지여도 [`live_session`](./live-session.md) 안에 둔다.** `self.user`는
  소켓이 열릴 때의 사용자다. 경계 밖의 소켓은 로그아웃을 듣지 않으므로, 다른 탭에서 로그아웃한 뒤에도 열린
  페이지의 섬은 옛 사용자로 `save_view`를 보낸다 — 매번 `self.user.is_authenticated`를 봐도 같은 스냅샷이다. 경계
  안에서는 로그인한 사용자의 로그아웃이 그 로그인으로 열린 소켓을 닫고, 다시 연결한 페이지는 새로 로드된다(익명
  방문자의 소켓은 로그아웃과 무관하다). 위의 `explore`처럼 `authorize` 없는 경계는
  누구나 들이면서 이 무효화만 얻는다.
- 로그인한 사용자만 들어오는 페이지는 경계에 술어를 준다: `live_session("account", authorize=lambda ctx:
  ctx.user.is_authenticated)`. 이름을 거는 것만으로는 로그인을 요구하지 않는다.

## 타입

`window.wireview`의 타입은 설치된 패키지의 `wireview/static/wireview/types.d.ts`에 있다(`WireviewIsland`,
`WireviewIslandContext`, `WireviewPushError`). 전역 선언 파일이므로 `tsconfig.json`의 `include`에 그 경로를 더하거나
복사해 둔다. 경로는 이렇게 찾는다.

```text
uv run python -c "import wireview, pathlib; print(pathlib.Path(wireview.__file__).parent / 'static/wireview/types.d.ts')"
```
