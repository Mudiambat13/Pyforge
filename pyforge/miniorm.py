"""miniorm — Mini ORM au-dessus de sqlite3 (stdlib).

    class User(Model):
        name = TextField(null=False)
        age = IntegerField(default=0)

    db = Database(":memory:")
    db.create_tables(User)
    User.create(name="Alice", age=30)
    User.select().where(age__gte=18).order_by("-age").limit(10).all()
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from contextlib import contextmanager
from typing import Any


# ---------------------------------------------------------------- champs
class Field:
    sql_type = "TEXT"

    def __init__(self, primary_key: bool = False, default: Any = None,
                 unique: bool = False, null: bool = True):
        self.primary_key = primary_key
        self.default = default
        self.unique = unique
        self.null = null and not primary_key
        self.name: str | None = None

    def get_default(self) -> Any:
        return self.default() if callable(self.default) else self.default

    def to_db(self, value: Any) -> Any:
        return value

    def from_db(self, value: Any) -> Any:
        return value


class IntegerField(Field):
    sql_type = "INTEGER"


class RealField(Field):
    sql_type = "REAL"


class TextField(Field):
    sql_type = "TEXT"


class BooleanField(Field):
    sql_type = "INTEGER"

    def to_db(self, value):
        return None if value is None else int(bool(value))

    def from_db(self, value):
        return None if value is None else bool(value)


class JSONField(Field):
    sql_type = "TEXT"

    def to_db(self, value):
        return None if value is None else json.dumps(value, ensure_ascii=False)

    def from_db(self, value):
        return None if value is None else json.loads(value)


class DateTimeField(Field):
    sql_type = "TEXT"

    def to_db(self, value):
        return value.isoformat() if isinstance(value, _dt.datetime) else value

    def from_db(self, value):
        return _dt.datetime.fromisoformat(value) if isinstance(value, str) else value


# ---------------------------------------------------------------- requêtes
_OPS = {"eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "like": "LIKE"}
_AGG = {"SUM", "AVG", "MIN", "MAX", "COUNT"}


class Query:
    """Requête chaînable. Les noms de colonnes sont validés contre le modèle :
    seules les valeurs transitent par les paramètres SQL (pas d'injection)."""

    def __init__(self, model: type["Model"]):
        self.model = model
        self._where: list[str] = []
        self._params: list[Any] = []
        self._order: list[str] = []
        self._limit: int | None = None
        self._offset: int | None = None

    def where(self, **conds: Any) -> "Query":
        for key, value in conds.items():
            name, _, op = key.partition("__")
            op = op or "eq"
            field = self.model._fields.get(name)
            if field is None:
                raise ValueError(f"Champ inconnu : {name!r} dans {self.model.__name__}")
            if op == "in":
                value = list(value)
                if not value:
                    self._where.append("0")
                    continue
                self._where.append(f"{name} IN ({','.join('?' * len(value))})")
                self._params.extend(field.to_db(v) for v in value)
            elif op == "isnull":
                self._where.append(f"{name} IS {'' if value else 'NOT '}NULL")
            elif op in _OPS:
                self._where.append(f"{name} {_OPS[op]} ?")
                self._params.append(field.to_db(value))
            else:
                raise ValueError(f"Opérateur inconnu : {op!r}")
        return self

    def order_by(self, *columns: str) -> "Query":
        for col in columns:
            name = col.lstrip("-")
            if name not in self.model._fields:
                raise ValueError(f"Champ inconnu : {name!r}")
            self._order.append(f"{name} {'DESC' if col.startswith('-') else 'ASC'}")
        return self

    def limit(self, n: int, offset: int | None = None) -> "Query":
        self._limit, self._offset = int(n), offset
        return self

    def _suffix(self, with_order: bool = True) -> tuple[str, list[Any]]:
        sql = f" FROM {self.model._table}"
        if self._where:
            sql += " WHERE " + " AND ".join(self._where)
        return sql, list(self._params)

    def all(self) -> list["Model"]:
        sql, params = self._suffix()
        sql = "SELECT *" + sql
        if self._order:
            sql += " ORDER BY " + ", ".join(self._order)
        if self._limit is not None:
            sql += f" LIMIT {self._limit}"
            if self._offset:
                sql += f" OFFSET {int(self._offset)}"
        rows = self.model._database().execute(sql, params).fetchall()
        return [self.model._from_row(r) for r in rows]

    def first(self) -> "Model | None":
        rows = Query.limit(self._clone(), 1).all()
        return rows[0] if rows else None

    def _clone(self) -> "Query":
        q = Query(self.model)
        q._where, q._params = list(self._where), list(self._params)
        q._order, q._limit, q._offset = list(self._order), self._limit, self._offset
        return q

    def count(self) -> int:
        sql, params = self._suffix()
        return self.model._database().execute("SELECT COUNT(*)" + sql, params).fetchone()[0]

    def aggregate(self, func: str, field: str) -> Any:
        func = func.upper()
        if func not in _AGG or field not in self.model._fields:
            raise ValueError("Agrégat ou champ invalide")
        sql, params = self._suffix()
        return self.model._database().execute(f"SELECT {func}({field})" + sql, params).fetchone()[0]

    def group_aggregate(self, group_field: str, func: str, field: str) -> list[tuple[Any, Any]]:
        """[(groupe, valeur)] triés par valeur décroissante."""
        func = func.upper()
        if func not in _AGG or group_field not in self.model._fields or field not in self.model._fields:
            raise ValueError("Agrégat ou champ invalide")
        sql, params = self._suffix()
        sql = f"SELECT {group_field}, {func}({field}) AS v" + sql + f" GROUP BY {group_field} ORDER BY v DESC"
        return [tuple(r) for r in self.model._database().execute(sql, params).fetchall()]

    def delete(self) -> int:
        sql, params = self._suffix()
        return self.model._database().execute("DELETE" + sql, params).rowcount

    def __iter__(self):
        return iter(self.all())


# ---------------------------------------------------------------- modèles
class ModelMeta(type):
    def __new__(mcs, name, bases, ns):
        fields = {k: v for k, v in ns.items() if isinstance(v, Field)}
        for k in fields:
            del ns[k]
        cls = super().__new__(mcs, name, bases, ns)
        if bases:  # ne pas traiter la classe Model elle-même
            inherited: dict[str, Field] = {}
            for base in bases:
                inherited.update(getattr(base, "_fields", {}))
            all_fields = {**inherited, **fields}
            if not any(f.primary_key for f in all_fields.values()):
                all_fields = {"id": IntegerField(primary_key=True), **all_fields}
            for k, f in all_fields.items():
                f.name = k
            cls._fields = all_fields
            cls._pk = next(k for k, f in all_fields.items() if f.primary_key)
            cls._table = ns.get("__table__", name.lower() + "s")
            cls._db = None
        return cls


class Model(metaclass=ModelMeta):
    _fields: dict[str, Field]
    _pk: str
    _table: str
    _db: "Database | None"

    def __init__(self, **kwargs: Any):
        for name, field in self._fields.items():
            setattr(self, name, kwargs.pop(name) if name in kwargs else field.get_default())
        if kwargs:
            raise TypeError(f"Champs inconnus pour {type(self).__name__} : {sorted(kwargs)}")

    @classmethod
    def _database(cls) -> "Database":
        if cls._db is None:
            raise RuntimeError(f"{cls.__name__} n'est lié à aucune base : db.create_tables({cls.__name__})")
        return cls._db

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> "Model":
        obj = cls.__new__(cls)
        for name, field in cls._fields.items():
            setattr(obj, name, field.from_db(row[name]))
        return obj

    # -- API publique
    @classmethod
    def create(cls, **kwargs: Any) -> "Model":
        obj = cls(**kwargs)
        obj.save()
        return obj

    @classmethod
    def select(cls) -> Query:
        return Query(cls)

    @classmethod
    def get(cls, **conds: Any) -> "Model | None":
        return cls.select().where(**conds).first()

    @classmethod
    def all(cls) -> list["Model"]:
        return cls.select().all()

    def save(self) -> "Model":
        db = self._database()
        pk_value = getattr(self, self._pk)
        names = [n for n in self._fields if not (n == self._pk and pk_value is None)]
        values = [self._fields[n].to_db(getattr(self, n)) for n in names]
        if pk_value is not None:
            sets = ", ".join(f"{n} = ?" for n in names if n != self._pk)
            params = [self._fields[n].to_db(getattr(self, n)) for n in names if n != self._pk]
            if sets and db.execute(
                f"UPDATE {self._table} SET {sets} WHERE {self._pk} = ?",
                params + [pk_value],
            ).rowcount:
                return self
        cur = db.execute(
            f"INSERT INTO {self._table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
            values,
        )
        if pk_value is None:
            setattr(self, self._pk, cur.lastrowid)
        return self

    def delete(self) -> None:
        self._database().execute(
            f"DELETE FROM {self._table} WHERE {self._pk} = ?", (getattr(self, self._pk),)
        )

    def to_dict(self) -> dict[str, Any]:
        return {n: getattr(self, n) for n in self._fields}

    def __repr__(self) -> str:
        inner = ", ".join(f"{n}={getattr(self, n)!r}" for n in self._fields)
        return f"{type(self).__name__}({inner})"


# ---------------------------------------------------------------- base
class Database:
    def __init__(self, path: str = ":memory:"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._depth = 0

    def create_tables(self, *models: type[Model]) -> None:
        for model in models:
            cols = []
            for name, f in model._fields.items():
                parts = [name, f.sql_type]
                if f.primary_key:
                    parts.append("PRIMARY KEY AUTOINCREMENT" if f.sql_type == "INTEGER" else "PRIMARY KEY")
                if not f.null and not f.primary_key:
                    parts.append("NOT NULL")
                if f.unique:
                    parts.append("UNIQUE")
                cols.append(" ".join(parts))
            model._db = self
            self.execute(f"CREATE TABLE IF NOT EXISTS {model._table} ({', '.join(cols)})")

    def execute(self, sql: str, params: Any = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, params)
        if self._depth == 0:
            self.conn.commit()
        return cur

    @contextmanager
    def transaction(self):
        """Tout ou rien : rollback si une exception sort du bloc."""
        self._depth += 1
        try:
            yield self
        except BaseException:
            self._depth -= 1
            if self._depth == 0:
                self.conn.rollback()
            raise
        else:
            self._depth -= 1
            if self._depth == 0:
                self.conn.commit()

    def close(self) -> None:
        self.conn.close()
