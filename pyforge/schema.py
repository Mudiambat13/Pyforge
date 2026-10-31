"""schema — Validation déclarative de données, imbriquées ou non.

    user = Schema({
        "name":  String(min_length=2),
        "email": Email(),
        "age":   Integer(min=0, max=130, coerce=True),
        "tags":  List(String(), required=False, default=list),
    })
    clean = user.validate(request_json)       # lève ValidationError
    errors = user.errors(request_json)        # ou dict {chemin: message}
"""
from __future__ import annotations

import re
from typing import Any, Callable

MISSING = object()


class ValidationError(ValueError):
    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))


class Validator:
    def __init__(self, required: bool = True, default: Any = MISSING, coerce: bool = False,
                 choices: list[Any] | tuple | set | None = None,
                 custom: Callable[[Any], bool] | None = None):
        self.required, self.default, self.coerce = required, default, coerce
        self.choices, self.custom = choices, custom

    def _convert(self, value: Any) -> Any:
        return value

    def _constraints(self, value: Any, path: str, errors: dict[str, str]) -> Any:
        return value

    def run(self, value: Any, path: str, errors: dict[str, str]) -> Any:
        if value is MISSING or value is None:
            if self.default is not MISSING:
                return self.default() if callable(self.default) else self.default
            if self.required:
                errors[path] = "champ requis"
            return None
        try:
            value = self._convert(value)
        except (TypeError, ValueError) as exc:
            errors[path] = str(exc)
            return None
        before = len(errors)
        value = self._constraints(value, path, errors)
        if len(errors) != before:
            return value
        if self.choices is not None and value not in self.choices:
            errors[path] = f"valeur non autorisée (attendu parmi {list(self.choices)})"
        elif self.custom is not None and self.custom(value) is False:
            errors[path] = "validation personnalisée échouée"
        return value


class String(Validator):
    def __init__(self, min_length: int = 0, max_length: int | None = None,
                 pattern: str | None = None, strip: bool = True, **kw: Any):
        super().__init__(**kw)
        self.min_length, self.max_length, self.strip = min_length, max_length, strip
        self.pattern = re.compile(pattern) if pattern else None

    def _convert(self, value):
        if not isinstance(value, str):
            if self.coerce and isinstance(value, (int, float)) and not isinstance(value, bool):
                value = str(value)
            else:
                raise TypeError("texte attendu")
        return value.strip() if self.strip else value

    def _constraints(self, value, path, errors):
        if len(value) < self.min_length:
            errors[path] = f"au moins {self.min_length} caractère(s) requis"
        elif self.max_length is not None and len(value) > self.max_length:
            errors[path] = f"au plus {self.max_length} caractère(s)"
        elif self.pattern and not self.pattern.fullmatch(value):
            errors[path] = "format invalide"
        return value


class Email(String):
    _RE = r"[^@\s]+@[^@\s]+\.[^@\s]{2,}"

    def __init__(self, **kw: Any):
        super().__init__(pattern=self._RE, **kw)

    def _constraints(self, value, path, errors):
        value = value.lower()
        if not self.pattern.fullmatch(value):
            errors[path] = "adresse e-mail invalide"
        return value


class _Number(Validator):
    kind = "nombre"

    def __init__(self, min: float | None = None, max: float | None = None, **kw: Any):
        super().__init__(**kw)
        self.min, self.max = min, max

    def _constraints(self, value, path, errors):
        if self.min is not None and value < self.min:
            errors[path] = f"doit être >= {self.min}"
        elif self.max is not None and value > self.max:
            errors[path] = f"doit être <= {self.max}"
        return value


class Integer(_Number):
    def _convert(self, value):
        if isinstance(value, bool):
            raise TypeError("entier attendu")
        if isinstance(value, int):
            return value
        if self.coerce and isinstance(value, (str, float)):
            try:
                f = float(value)
            except ValueError:
                raise ValueError("entier attendu") from None
            if f != int(f):
                raise ValueError("entier attendu")
            return int(f)
        raise TypeError("entier attendu")


class Float(_Number):
    def _convert(self, value):
        if isinstance(value, bool):
            raise TypeError("nombre attendu")
        if isinstance(value, (int, float)):
            return float(value)
        if self.coerce and isinstance(value, str):
            try:
                return float(value.replace(",", "."))
            except ValueError:
                raise ValueError("nombre attendu") from None
        raise TypeError("nombre attendu")


class Boolean(Validator):
    _TRUE = {"true", "1", "oui", "yes", "y", "on", "vrai"}
    _FALSE = {"false", "0", "non", "no", "n", "off", "faux"}

    def _convert(self, value):
        if isinstance(value, bool):
            return value
        if self.coerce and isinstance(value, (str, int)):
            s = str(value).strip().lower()
            if s in self._TRUE:
                return True
            if s in self._FALSE:
                return False
        raise TypeError("booléen attendu")


class List(Validator):
    def __init__(self, item: Validator, min_items: int = 0, max_items: int | None = None, **kw: Any):
        super().__init__(**kw)
        self.item, self.min_items, self.max_items = item, min_items, max_items

    def _convert(self, value):
        if not isinstance(value, (list, tuple)):
            raise TypeError("liste attendue")
        return list(value)

    def _constraints(self, value, path, errors):
        if len(value) < self.min_items:
            errors[path] = f"au moins {self.min_items} élément(s)"
            return value
        if self.max_items is not None and len(value) > self.max_items:
            errors[path] = f"au plus {self.max_items} élément(s)"
            return value
        return [self.item.run(v, f"{path}[{i}]", errors) for i, v in enumerate(value)]


class Object(Validator):
    def __init__(self, fields: dict[str, Validator], allow_extra: bool = False, **kw: Any):
        super().__init__(**kw)
        self.fields, self.allow_extra = fields, allow_extra

    def _convert(self, value):
        if not isinstance(value, dict):
            raise TypeError("objet attendu")
        return value

    def _constraints(self, value, path, errors):
        out: dict[str, Any] = {}
        for key, validator in self.fields.items():
            sub = f"{path}.{key}" if path else key
            cleaned = validator.run(value.get(key, MISSING), sub, errors)
            if cleaned is not None or key in value:
                out[key] = cleaned
        for key in value.keys() - self.fields.keys():
            if self.allow_extra:
                out[key] = value[key]
            else:
                errors[f"{path}.{key}" if path else key] = "champ inconnu"
        return out


class Schema(Object):
    """Schéma racine : `validate` retourne les données nettoyées ou lève
    `ValidationError` avec toutes les erreurs (pas seulement la première)."""

    def validate(self, data: Any) -> dict[str, Any]:
        errors: dict[str, str] = {}
        cleaned = self.run(data, "", errors)
        if errors:
            raise ValidationError({k or "(racine)": v for k, v in errors.items()})
        return cleaned

    def errors(self, data: Any) -> dict[str, str]:
        try:
            self.validate(data)
        except ValidationError as exc:
            return exc.errors
        return {}

    def is_valid(self, data: Any) -> bool:
        return not self.errors(data)
