from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Column

from testpaper_backend.db import Base


@dataclass
class TableState:
    columns: set[str]
    indexes: set[str] = field(default_factory=set)
    constraints: set[str] = field(default_factory=set)


class FakeResult:
    def __init__(self, rows: list[tuple[int]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[int]]:
        return self._rows


class FakeConnection:
    def __init__(self, operation: MigrationOperation) -> None:
        self.operation = operation

    def execute(self, statement: Any, parameters: dict[str, Any] | None = None) -> FakeResult:
        sql = str(statement)
        match = re.search(r'SELECT id FROM (\w+) WHERE "publicId" IS NULL', sql)
        if match:
            table = match.group(1)
            return FakeResult([(row_id,) for row_id in self.operation.row_ids.get(table, [])])
        self.operation.execute(statement)
        return FakeResult([])


class MigrationOperation:
    def __init__(self) -> None:
        self.tables: dict[str, TableState] = {}
        self.row_ids: dict[str, list[int]] = {}
        self.executed_sql: list[str] = []

    @staticmethod
    def f(name: str) -> str:
        return name

    def create_table(self, name: str, *items: Any, **_: Any) -> None:
        if name in self.tables:
            raise AssertionError(f"table already exists: {name}")
        columns = {item.name for item in items if isinstance(item, Column)}
        self.tables[name] = TableState(columns=columns)
        self.row_ids[name] = []

    def drop_table(self, name: str) -> None:
        self._table(name)
        del self.tables[name]
        self.row_ids.pop(name, None)

    def add_column(self, table_name: str, column: Column) -> None:
        table = self._table(table_name)
        if column.name in table.columns:
            raise AssertionError(f"column already exists: {table_name}.{column.name}")
        table.columns.add(column.name)

    def drop_column(self, table_name: str, column_name: str) -> None:
        table = self._table(table_name)
        if column_name not in table.columns:
            raise AssertionError(f"column does not exist: {table_name}.{column_name}")
        table.columns.remove(column_name)

    def alter_column(self, table_name: str, column_name: str, **_: Any) -> None:
        table = self._table(table_name)
        if column_name not in table.columns:
            raise AssertionError(f"cannot alter missing column: {table_name}.{column_name}")

    def create_index(self, name: str, table_name: str, columns: list[str], **kwargs: Any) -> None:
        table = self._table(table_name)
        missing = set(columns) - table.columns
        if missing:
            raise AssertionError(f"index {name} references missing columns: {sorted(missing)}")
        if name in table.indexes and not kwargs.get("if_not_exists"):
            raise AssertionError(f"index already exists: {name}")
        table.indexes.add(name)

    def drop_index(self, name: str, table_name: str | None = None, **kwargs: Any) -> None:
        table = self._find_index_table(name, table_name)
        if table is None:
            if kwargs.get("if_exists"):
                return
            raise AssertionError(f"index does not exist: {name}")
        table.indexes.remove(name)

    def create_unique_constraint(self, name: str, table_name: str, columns: list[str]) -> None:
        table = self._table(table_name)
        missing = set(columns) - table.columns
        if missing:
            raise AssertionError(f"constraint {name} references missing columns: {sorted(missing)}")
        if name in table.constraints:
            raise AssertionError(f"constraint already exists: {name}")
        table.constraints.add(name)

    def create_foreign_key(
        self,
        name: str,
        source_table: str,
        referent_table: str,
        local_cols: list[str],
        remote_cols: list[str],
        **_: Any,
    ) -> None:
        source = self._table(source_table)
        referent = self._table(referent_table)
        missing_local = set(local_cols) - source.columns
        missing_remote = set(remote_cols) - referent.columns
        if missing_local or missing_remote:
            raise AssertionError(
                f"foreign key {name} references missing columns: local={sorted(missing_local)}, remote={sorted(missing_remote)}"
            )
        if name in source.constraints:
            raise AssertionError(f"constraint already exists: {name}")
        source.constraints.add(name)

    def drop_constraint(self, name: str, table_name: str, **_: Any) -> None:
        table = self._table(table_name)
        if name not in table.constraints:
            raise AssertionError(f"constraint does not exist: {name}")
        table.constraints.remove(name)

    def get_bind(self) -> FakeConnection:
        return FakeConnection(self)

    def execute(self, statement: Any) -> None:
        sql = str(statement).strip()
        self.executed_sql.append(sql)
        normalized = " ".join(sql.split())

        if normalized.startswith("INSERT INTO questions"):
            ids = [int(value) for value in re.findall(r"(?m)^\s*\((\d+),", sql)]
            if len(ids) != 10:
                raise AssertionError(f"expected 10 seeded questions, found {len(ids)}")
            self.row_ids["questions"] = ids
            return

        create_index = re.match(r"CREATE INDEX IF NOT EXISTS (\w+) ON (\w+)", normalized, re.IGNORECASE)
        if create_index:
            table = self._table(create_index.group(2))
            table.indexes.add(create_index.group(1))
            return

        drop_index = re.match(r"DROP INDEX IF EXISTS (\w+)", normalized, re.IGNORECASE)
        if drop_index:
            table = self._find_index_table(drop_index.group(1), None)
            if table is not None:
                table.indexes.remove(drop_index.group(1))
            return

        alter_add = re.match(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", normalized, re.IGNORECASE)
        if alter_add:
            table = self._table(alter_add.group(1))
            column_name = alter_add.group(2)
            if column_name in table.columns:
                raise AssertionError(f"column already exists: {alter_add.group(1)}.{column_name}")
            table.columns.add(column_name)
            return

        alter_drop = re.match(r"ALTER TABLE (\w+) DROP COLUMN (\w+)", normalized, re.IGNORECASE)
        if alter_drop:
            self.drop_column(alter_drop.group(1), alter_drop.group(2))
            return

        alter_rename = re.match(
            r"ALTER TABLE (\w+) RENAME COLUMN (\w+) TO (\w+)",
            normalized,
            re.IGNORECASE,
        )
        if alter_rename:
            table = self._table(alter_rename.group(1))
            old_name, new_name = alter_rename.group(2), alter_rename.group(3)
            if old_name not in table.columns or new_name in table.columns:
                raise AssertionError(f"invalid column rename: {old_name} -> {new_name}")
            table.columns.remove(old_name)
            table.columns.add(new_name)
            return

        if normalized.startswith("DO $$") and "column_name = 'subjects'" in normalized:
            table = self._table("questions")
            if 'ADD COLUMN "subjects"' in normalized and "subjects" not in table.columns:
                table.columns.add("subjects")
            if 'ADD COLUMN "subject"' in normalized and "subject" not in table.columns:
                table.columns.add("subject")
            if 'DROP COLUMN "subject"' in normalized and "subject" in table.columns:
                table.columns.remove("subject")
            if 'DROP COLUMN "subjects"' in normalized and "subjects" in table.columns:
                table.columns.remove("subjects")

    def _table(self, name: str) -> TableState:
        if name not in self.tables:
            raise AssertionError(f"table does not exist: {name}")
        return self.tables[name]

    def _find_index_table(self, name: str, table_name: str | None) -> TableState | None:
        if table_name is not None:
            table = self._table(table_name)
            return table if name in table.indexes else None
        return next((table for table in self.tables.values() if name in table.indexes), None)


def load_migrations(project_root: Path) -> list[ModuleType]:
    modules: dict[str, ModuleType] = {}
    for path in sorted((project_root / "alembic" / "versions").glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load migration: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules[module.revision] = module

    roots = [module for module in modules.values() if module.down_revision is None]
    if len(roots) != 1:
        raise AssertionError(f"expected one migration root, found {len(roots)}")

    ordered = [roots[0]]
    while len(ordered) < len(modules):
        children = [module for module in modules.values() if module.down_revision == ordered[-1].revision]
        if len(children) != 1:
            raise AssertionError(f"migration {ordered[-1].revision} has {len(children)} children")
        ordered.append(children[0])
    return ordered


def simulate_migrations(project_root: Path | None = None) -> dict[str, Any]:
    root = project_root or Path(__file__).resolve().parents[1]
    migrations = load_migrations(root)
    operation = MigrationOperation()

    for migration in migrations:
        migration.op = operation
        migration.upgrade()

    model_columns = {table.name: {column.name for column in table.columns} for table in Base.metadata.sorted_tables}
    simulated_columns = {name: state.columns for name, state in operation.tables.items()}
    if simulated_columns != model_columns:
        raise AssertionError(f"migration/model schema mismatch: simulated={simulated_columns}, models={model_columns}")
    if operation.row_ids["users"]:
        raise AssertionError("fresh migrations must not seed users")
    if operation.row_ids["questions"] != list(range(1, 11)):
        raise AssertionError("fresh migrations must seed exactly questions 1 through 10")

    final_revision = migrations[-1].revision
    for migration in reversed(migrations):
        migration.downgrade()
    if operation.tables:
        raise AssertionError(f"downgrade left tables behind: {sorted(operation.tables)}")

    return {
        "migrationCount": len(migrations),
        "head": final_revision,
        "seedUsers": 0,
        "seedQuestions": 10,
        "downgradeClean": True,
    }


def main() -> None:
    report = simulate_migrations()
    print(
        "Migration simulation passed: "
        f"{report['migrationCount']} revisions, head {report['head']}, "
        f"{report['seedUsers']} users, {report['seedQuestions']} questions, clean downgrade"
    )


if __name__ == "__main__":
    main()
