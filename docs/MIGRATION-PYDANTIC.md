# Pydantic v1 → v2 마이그레이션 가이드

> django-reactor를 Pydantic v2로 업그레이드하기 위한 상세 가이드

---

## 1. 개요

### 1.1 현재 상태

```toml
# setup.cfg
install_requires =
    pydantic>=1.8,<2  # Pydantic v1
```

### 1.2 목표 상태

```toml
# setup.cfg
install_requires =
    pydantic>=2.0,<3  # Pydantic v2
```

### 1.3 주요 변경 영향

| 파일 | 영향도 | 주요 변경 |
|------|:------:|----------|
| `component.py` | 높음 | BaseModel 설정, ModelField, validate_arguments |
| `serializer.py` | 중간 | .dict() → .model_dump() |
| `repository.py` | 낮음 | 타입 힌트 업데이트 |
| `schemas.py` | 낮음 | Enum 처리 |

---

## 2. 핵심 변경 사항

### 2.1 Model Config 변경

**Before (v1)**:
```python
class Component(BaseModel):
    class Config:
        arbitrary_types_allowed = True
        validate_assignment = True
        json_encoders = {
            models.Model: lambda x: x.pk,
            models.QuerySet: lambda qs: {...},
        }
```

**After (v2)**:
```python
from pydantic import ConfigDict, field_serializer

class Component(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,
    )

    @field_serializer('user')
    def serialize_user(self, user):
        if hasattr(user, 'pk'):
            return user.pk
        return None

    # QuerySet 직렬화는 별도 처리 필요
```

---

### 2.2 Field 정의 변경

**Before (v1)**:
```python
from pydantic import Field
from pydantic.fields import ModelField

id: str = Field(default_factory=lambda: f"rx-{uuid4()}")

# ModelField 사용
for field in cls.__fields__.values():
    if field.pre_validators is None:
        ...
```

**After (v2)**:
```python
from pydantic import Field
from pydantic.fields import FieldInfo

id: str = Field(default_factory=lambda: f"rx-{uuid4()}")

# FieldInfo 사용
for field_name, field_info in cls.model_fields.items():
    # v2에서는 pre_validators 대신 다른 접근 필요
    ...
```

---

### 2.3 validate_arguments 대체

**Before (v1)**:
```python
from pydantic import validate_arguments

@validate_arguments(config={"arbitrary_types_allowed": True})
def increment(self, amount: int = 1):
    self.count += amount
```

**After (v2)**:
```python
from pydantic import validate_call

@validate_call(config={"arbitrary_types_allowed": True})
def increment(self, amount: int = 1):
    self.count += amount
```

**주의**: `validate_arguments`는 v2에서 `validate_call`로 이름 변경됨

---

### 2.4 .dict() → .model_dump()

**Before (v1)**:
```python
state = component.dict(exclude=self._exclude_fields)
json_state = component.json(exclude=self._exclude_fields)
```

**After (v2)**:
```python
state = component.model_dump(exclude=self._exclude_fields)
json_state = component.model_dump_json(exclude=self._exclude_fields)
```

---

### 2.5 validator → field_validator

**Before (v1)**:
```python
from pydantic import validator

class Component(BaseModel):
    @validator('count', pre=True)
    def validate_count(cls, v):
        return int(v) if v else 0
```

**After (v2)**:
```python
from pydantic import field_validator

class Component(BaseModel):
    @field_validator('count', mode='before')
    @classmethod
    def validate_count(cls, v):
        return int(v) if v else 0
```

---

### 2.6 root_validator → model_validator

**Before (v1)**:
```python
from pydantic import root_validator

class Component(BaseModel):
    @root_validator(pre=True)
    def validate_all(cls, values):
        return values
```

**After (v2)**:
```python
from pydantic import model_validator

class Component(BaseModel):
    @model_validator(mode='before')
    @classmethod
    def validate_all(cls, values):
        return values
```

---

## 3. component.py 상세 마이그레이션

### 3.1 현재 코드 분석

```python
# 현재 component.py의 주요 Pydantic v1 사용 부분

# 1. Config 클래스
class Component(BaseModel):
    class Config:
        arbitrary_types_allowed = True
        validate_assignment = True
        json_encoders = {
            models.Model: lambda x: x.pk,
            models.QuerySet: lambda qs: {
                "app": qs.model._meta.app_label,
                "model": qs.model._meta.model_name,
                "ids": [x.pk for x in qs],
            },
        }

# 2. __init_subclass__에서 validate_arguments 사용
def __init_subclass__(cls, name=None, public=True):
    ...
    setattr(
        cls,
        attr_name,
        validate_arguments(
            config={"arbitrary_types_allowed": True}
        )(attr),
    )

# 3. ModelField 접근
for field in cls.__fields__.values():
    if field.pre_validators is None:
        ...
        if is_model:
            field.pre_validators = [load_model_instance]

# 4. 커스텀 로더 (pre_validators)
def load_model_instance(model, v, fields, field: ModelField, config):
    if v is None or isinstance(v, field.type_):
        return v
    else:
        return field.type_.objects.filter(pk=v).first()
```

### 3.2 마이그레이션된 코드

```python
"""
component.py - Pydantic v2 버전
"""
import typing as t
from functools import reduce
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    field_serializer,
    model_validator,
    validate_call,
)
from django.apps import apps
from django.db import models

# 타입 정의
ComponentState = dict[str, t.Any]


class Component(BaseModel):
    """Pydantic v2 기반 컴포넌트"""

    # v2 스타일 설정
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,
        # json_encoders는 field_serializer로 대체
    )

    # 클래스 레벨 속성
    _all: t.ClassVar[dict[str, type["Component"]]] = {}
    _name: t.ClassVar[str]
    _template_name: t.ClassVar[str]
    _exclude_fields: t.ClassVar[set[str]] = {"user", "reactor"}
    _subscriptions: t.ClassVar[set[str]] = set()

    # 인스턴스 필드
    id: str = Field(default_factory=lambda: f"rx-{uuid4()}")
    user: t.Any  # Django User (AbstractBaseUser | AnonymousUser)
    reactor: "ReactorMeta"

    # 직렬화 설정
    @field_serializer('user')
    def serialize_user(self, user: t.Any) -> int | None:
        """Django User를 PK로 직렬화"""
        if hasattr(user, 'pk'):
            return user.pk
        return None

    def __init_subclass__(
        cls,
        name: str | None = None,
        public: bool = True,
        **kwargs,
    ):
        super().__init_subclass__(**kwargs)

        if public:
            name = name or cls.__name__
            cls._all[name] = cls
            cls._name = name
            cls._fqn = f"{cls.__module__}.{name}"

        # 메서드에 validate_call 적용
        for attr_name in vars(cls):
            attr = getattr(cls, attr_name)
            if (
                not attr_name.startswith("_")
                and attr_name.islower()
                and callable(attr)
            ):
                setattr(
                    cls,
                    attr_name,
                    validate_call(
                        config={"arbitrary_types_allowed": True}
                    )(attr),
                )

    @model_validator(mode='before')
    @classmethod
    def load_django_models(cls, data: dict[str, t.Any]) -> dict[str, t.Any]:
        """Django 모델 인스턴스 자동 로딩"""
        for field_name, field_info in cls.model_fields.items():
            if field_name not in data:
                continue

            field_type = field_info.annotation
            value = data[field_name]

            # None이거나 이미 올바른 타입이면 스킵
            if value is None:
                continue

            # Model 타입 처리
            try:
                if isinstance(field_type, type) and issubclass(field_type, models.Model):
                    if not isinstance(value, field_type):
                        data[field_name] = field_type.objects.filter(pk=value).first()
            except TypeError:
                pass

            # QuerySet 타입 처리
            if isinstance(value, dict) and "app" in value and "model" in value:
                model_class = apps.get_model(value["app"], value["model"])
                data[field_name] = model_class.objects.filter(pk__in=value.get("ids", []))

        return data

    # 직렬화 메서드
    def get_state(self) -> ComponentState:
        """컴포넌트 상태 반환 (직렬화용)"""
        return self.model_dump(
            exclude=self._exclude_fields,
            mode='json',
        )

    def get_state_json(self) -> str:
        """컴포넌트 상태를 JSON 문자열로 반환"""
        return self.model_dump_json(
            exclude=self._exclude_fields,
        )

    # ... 나머지 메서드들은 동일
```

### 3.3 QuerySet 직렬화 헬퍼

```python
# serializer.py 또는 component.py에 추가

from typing import Any
from django.db.models import QuerySet

def serialize_queryset(qs: QuerySet) -> dict[str, Any]:
    """QuerySet을 직렬화 가능한 딕셔너리로 변환"""
    return {
        "app": qs.model._meta.app_label,
        "model": qs.model._meta.model_name,
        "ids": list(qs.values_list('pk', flat=True)),
    }

def deserialize_queryset(data: dict[str, Any]) -> QuerySet:
    """딕셔너리를 QuerySet으로 복원"""
    from django.apps import apps
    model_class = apps.get_model(data["app"], data["model"])
    return model_class.objects.filter(pk__in=data.get("ids", []))


# Component에서 사용
class Component(BaseModel):
    items: QuerySet | None = None

    @field_serializer('items')
    def serialize_items(self, items: QuerySet | None) -> dict | None:
        if items is None:
            return None
        return serialize_queryset(items)

    @field_validator('items', mode='before')
    @classmethod
    def validate_items(cls, v):
        if isinstance(v, dict) and "app" in v:
            return deserialize_queryset(v)
        return v
```

---

## 4. 단계별 마이그레이션 절차

### Step 1: 의존성 업데이트

```bash
# setup.cfg 수정
# pydantic>=1.8,<2 → pydantic>=2.0,<3

# 설치
pip install -e ".[dev]"
```

### Step 2: Config 클래스 변환

```python
# Before
class Config:
    arbitrary_types_allowed = True

# After
model_config = ConfigDict(arbitrary_types_allowed=True)
```

### Step 3: validate_arguments → validate_call

```python
# Before
from pydantic import validate_arguments

# After
from pydantic import validate_call
```

### Step 4: ModelField 접근 방식 변경

```python
# Before
for field in cls.__fields__.values():
    field.pre_validators = [...]

# After
# model_validator를 사용하여 전처리
@model_validator(mode='before')
@classmethod
def preprocess(cls, data):
    ...
```

### Step 5: json_encoders → field_serializer

```python
# Before
class Config:
    json_encoders = {Model: lambda x: x.pk}

# After
@field_serializer('user')
def serialize_user(self, user):
    return user.pk
```

### Step 6: .dict() / .json() 메서드 변경

```bash
# 전체 검색 및 치환
grep -r "\.dict(" reactor/ --include="*.py"
grep -r "\.json(" reactor/ --include="*.py"

# .dict() → .model_dump()
# .json() → .model_dump_json()
```

### Step 7: 테스트 실행

```bash
pytest tests/ -v

# 실패하는 테스트 확인 및 수정
```

---

## 5. 호환성 레이어 (선택적)

점진적 마이그레이션을 위한 호환성 레이어:

```python
# compat.py
import sys
from importlib.metadata import version

PYDANTIC_VERSION = int(version("pydantic").split(".")[0])

if PYDANTIC_VERSION >= 2:
    from pydantic import (
        ConfigDict,
        field_validator,
        field_serializer,
        model_validator,
        validate_call,
    )

    def get_model_fields(model_class):
        return model_class.model_fields

    def model_dump(instance, **kwargs):
        return instance.model_dump(**kwargs)

    def model_dump_json(instance, **kwargs):
        return instance.model_dump_json(**kwargs)

else:
    # v1 호환
    from pydantic import validator as field_validator
    from pydantic import root_validator as model_validator
    from pydantic import validate_arguments as validate_call

    ConfigDict = dict  # type: ignore

    def field_serializer(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

    def get_model_fields(model_class):
        return model_class.__fields__

    def model_dump(instance, **kwargs):
        return instance.dict(**kwargs)

    def model_dump_json(instance, **kwargs):
        return instance.json(**kwargs)
```

**사용**:
```python
from .compat import (
    ConfigDict,
    field_validator,
    model_dump,
    PYDANTIC_VERSION,
)

class Component(BaseModel):
    if PYDANTIC_VERSION >= 2:
        model_config = ConfigDict(arbitrary_types_allowed=True)
    else:
        class Config:
            arbitrary_types_allowed = True
```

---

## 6. 테스트 체크리스트

- [ ] 컴포넌트 생성 테스트
- [ ] 상태 직렬화/역직렬화 테스트
- [ ] Django 모델 필드 로딩 테스트
- [ ] QuerySet 직렬화 테스트
- [ ] validate_call 데코레이터 테스트
- [ ] WebSocket 메시지 처리 테스트
- [ ] 기존 예제 앱 동작 확인

---

## 7. 주의사항

### 7.1 Breaking Changes

1. **json_encoders 제거**: 모든 커스텀 직렬화는 `@field_serializer`로
2. **pre_validators 제거**: `@model_validator(mode='before')`로 대체
3. **__fields__ → model_fields**: 필드 접근 방식 변경
4. **validate_arguments → validate_call**: 함수명 변경

### 7.2 성능 고려

- Pydantic v2는 Rust 기반 `pydantic-core`로 2-50x 빠름
- 마이그레이션 후 성능 향상 기대

### 7.3 타입 힌트 강화

```python
# v2에서 더 엄격한 타입 검사
from typing import ClassVar

_all: ClassVar[dict[str, type["Component"]]] = {}  # ClassVar 필수
```

---

## 8. 참고 자료

- [Pydantic v2 Migration Guide](https://docs.pydantic.dev/latest/migration/)
- [Pydantic v2 Changelog](https://docs.pydantic.dev/latest/changelog/)
- [bump-pydantic 자동 마이그레이션 도구](https://github.com/pydantic/bump-pydantic)

```bash
# 자동 마이그레이션 도구 사용
pip install bump-pydantic
bump-pydantic reactor/
```

---

*이 가이드는 django-reactor의 Pydantic v2 마이그레이션을 위한 참조 문서입니다.*
