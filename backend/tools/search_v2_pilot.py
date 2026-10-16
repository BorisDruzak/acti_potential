"""Foreground synthetic SEARCH-001 pilot in a fresh disposable PostgreSQL database.

Uses the existing isolated test cluster URL, never the application database.
Creates and drops only its own UUID-named database. No real documents or LLMs.
"""

import argparse
import gc
import importlib.util
import json
import math
import os
import resource
import time
import tracemalloc
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from sqlalchemy import create_engine, event, func, insert, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.content.contracts import SearchInput
from app.content.engine import candidate_query, execute_search
from app.content.models import ContentExtraction, DocumentBlock, MetadataFragment
from app.db.migrate import upgrade_database
from app.db.session import validate_test_database
from app.identity.models import User
from app.identity.policy import Actor
from app.registry.models import (
    Document,
    DocumentFile,
    DocumentType,
    DocumentVersion,
    FormSchema,
    Registry,
    RegistryAccess,
)


def seed_base(factory):
    with factory.begin() as db:
        user = User(display_name="Synthetic search pilot viewer")
        registry = Registry(code="search-pilot", name="Synthetic search pilot")
        dtype = DocumentType(code="search-pilot", name="Synthetic pilot document")
        db.add_all([user, registry, dtype])
        db.flush()
        registry.document_type_ids = [str(dtype.id)]
        form = FormSchema(
            document_type_id=dtype.id,
            state="published",
            version=1,
            schema_data={
                "sections": [
                    {
                        "fields": [
                            {
                                "key": "title",
                                "label": "Название",
                                "type": "text",
                                "system": "title",
                            },
                            {
                                "key": "number",
                                "label": "Номер",
                                "type": "text",
                                "system": "reg_number",
                            },
                            {"key": "public", "label": "Реквизиты", "type": "text"},
                            {
                                "key": "secret",
                                "label": "Закрыто",
                                "type": "text",
                                "read_roles": ["admin"],
                            },
                        ]
                    }
                ]
            },
        )
        db.add(form)
        db.flush()
        db.add(
            RegistryAccess(
                registry_id=registry.id, user_id=user.id, permissions=["read", "download"]
            )
        )
        return user.id, registry.id, dtype.id, form.id


def seed(factory, base, start, end, file_stride=1000):
    user, registry, dtype, form = base
    for low in range(start, end, 2500):
        docs, fragments, versions, files, extractions, blocks = [], [], [], [], [], []
        for index in range(low, min(end, low + 2500)):
            identity = uuid4()
            title = "Содержание территорий" if index % 1000 == 0 else f"Архивный документ {index}"
            number = f"П-{index:06d}/2026"
            body = (
                "Жилищно-коммунальное хозяйство и благоустройство"
                if index % 1000 == 0
                else f"Плановое архивное дело категории {index % 127}"
            )
            docs.append(
                dict(
                    id=identity,
                    registry_id=registry,
                    document_type_id=dtype,
                    schema_version_id=form,
                    created_by=user,
                    current_version=1,
                    title=title,
                    reg_number=number,
                    revision=1,
                    custom_data={"public": body, "secret": "Скрытыйсигнал"},
                )
            )
            for key, value in (
                ("title", title),
                ("number", number),
                ("public", body),
                ("secret", "Скрытыйсигнал"),
            ):
                fragments.append(
                    dict(
                        id=uuid4(),
                        document_id=identity,
                        document_revision=1,
                        field_key=key,
                        label=key,
                        text=value,
                    )
                )
            if index % file_stride == 0:
                vid, fid, eid = uuid4(), uuid4(), uuid4()
                versions.append(dict(id=vid, document_id=identity, version=1, created_by=user))
                files.append(
                    dict(
                        id=fid,
                        document_id=identity,
                        version_id=vid,
                        name="synthetic.pdf",
                        size=1,
                        mime="application/pdf",
                        sha256="0" * 64,
                        storage_key=f"synthetic/{fid}",
                        created_by=user,
                    )
                )
                extractions.append(
                    dict(
                        id=eid,
                        document_id=identity,
                        file_id=fid,
                        version_id=vid,
                        sha256="0" * 64,
                        fingerprint=eid.hex,
                        parser="pdf",
                        parser_version="synthetic-pilot",
                        state="completed",
                        full_text="",
                    )
                )
                for ordinal, value in enumerate(
                    (
                        "Преамбула синтетического акта",
                        "Уникальная фраза из середины файла о содержании территорий",
                        "Таблица расходов на озеленение",
                    )
                ):
                    blocks.append(
                        dict(
                            id=uuid4(),
                            extraction_id=eid,
                            document_id=identity,
                            kind="table_row" if ordinal == 2 else "paragraph",
                            ordinal=ordinal,
                            page=ordinal + 1,
                            anchor=f"page:{ordinal + 1}",
                            text=value
                            if index % 1000 == 0
                            else f"Архивный нейтральный блок {index} {ordinal}",
                        )
                    )
        with factory.begin() as db:
            for model, rows in (
                (Document, docs),
                (MetadataFragment, fragments),
                (DocumentVersion, versions),
                (DocumentFile, files),
                (ContentExtraction, extractions),
                (DocumentBlock, blocks),
            ):
                if rows:
                    db.execute(insert(model), rows)
    with factory.begin() as db:
        for table in ("documents", "content_metadata_fragments", "document_blocks"):
            db.execute(text("ANALYZE " + table))


def explain(engine, actor, params, *, count=False, jit=None):
    with sessionmaker(engine)() as db:
        if jit is not None:
            db.execute(select(func.set_config("jit", "on" if jit else "off", True)))
        stmt, _, _ = candidate_query(db, actor, params)
        stmt = (
            select(func.count()).select_from(stmt.order_by(None).subquery())
            if count
            else stmt.order_by(None).with_only_columns(Document.id).limit(20)
        )
        compiled = stmt.compile(dialect=engine.dialect, compile_kwargs={"render_postcompile": True})
        values = {
            key: Jsonb(value)
            if key in compiled.binds and isinstance(compiled.binds[key].type, JSONB)
            else value
            for key, value in compiled.params.items()
        }
        plan = (
            db.connection()
            .exec_driver_sql("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + str(compiled), values)
            .scalar()
        )
        return plan[0]


def measured(factory, actor, params, samples, jit_off=False):
    durations, statements = [], []
    engine = factory.kw["bind"]
    counts = [0]

    def record(_conn, _cursor, statement, *_):
        if statement.lstrip().upper().startswith("SELECT"):
            counts[0] += 1

    event.listen(engine, "before_cursor_execute", record)
    try:
        total = None
        for _ in range(samples):
            counts[0] = 0
            started = time.perf_counter()
            with factory.begin() as db:
                if jit_off:
                    db.execute(select(func.set_config("jit", "off", True)))
                try:
                    result = execute_search(db, actor, params, secret="synthetic-pilot-only")
                except (DBAPIError, HTTPException) as error:
                    if not (
                        isinstance(error, DBAPIError)
                        and getattr(error.orig, "sqlstate", None) == "57014"
                        or isinstance(error, HTTPException)
                        and isinstance(error.detail, dict)
                        and error.detail.get("code") == "search_query_too_broad"
                    ):
                        raise
                    db.rollback()
                    return {
                        "status": "statement_timeout",
                        "statement_timeout_ms": 8000,
                        "samples_completed": len(durations),
                    }
            total = result["total"]
            durations.append(round((time.perf_counter() - started) * 1000, 2))
            statements.append(counts[0])
        return {
            "total": total,
            "p50_ms": sorted(durations)[len(durations) // 2],
            "p95_ms": sorted(durations)[math.ceil(len(durations) * 0.95) - 1],
            "samples": len(durations),
            "select_min": min(statements),
            "select_max": max(statements),
        }
    finally:
        event.remove(engine, "before_cursor_execute", record)


def run(args):
    source = os.environ["ACTI_DATABASE_URL"]
    validate_test_database(source)
    url = make_url(source)
    identity = uuid4().hex
    database = f"acti_search_{identity}_test"
    admin = create_engine(url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    target = create_engine(url.set(database=database), hide_parameters=True)
    created = False
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{database}"'))
        created = True
        upgrade_database(target)
        factory = sessionmaker(target, expire_on_commit=False)
        base = seed_base(factory)
        actor = Actor(base[0], "Synthetic viewer", frozenset({"viewer"}))
        previous, reports = 0, []
        baseline = None
        if args.baseline:
            spec = importlib.util.spec_from_file_location("baseline_search", args.baseline)
            baseline = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(baseline)
        queries = [
            ("number", "П-000000/2026", False),
            ("title", "содержание территорий", False),
            ("morphology", "благоустройства", False),
            ("fuzzy", "благоустроиство", True),
            ("synonym", "ЖКХ", False),
            ("file_phrase", '"уникальная фраза"', False),
            ("table", "озеленения", False),
            ("hidden", "Скрытыйсигнал", False),
        ]
        for size in args.sizes:
            seed(factory, base, previous, size, args.file_stride)
            previous = size
            report = {"documents": size, "queries": {}}
            for name, query, fuzzy in queries:
                params = SearchInput(query=query, fuzzy=fuzzy, page_size=20)
                report["queries"][name] = measured(
                    factory, actor, params, args.samples, args.jit_off
                )
            report["plans"] = {
                name: explain(target, actor, SearchInput(query=query, fuzzy=fuzzy))
                for name, query, fuzzy in queries
                if name in {"number", "title", "fuzzy", "file_phrase"}
            }
            report["broad"] = measured(
                factory, actor, SearchInput(query="архивный", fuzzy=False), 2
            )
            if baseline:
                timings = []
                for _ in range(3 if size <= 10000 else 1):
                    started = time.perf_counter()
                    try:
                        with factory() as db:
                            result = baseline.search_response(
                                db,
                                actor,
                                baseline.ContentSearchParameters(
                                    text="содержание территорий", fuzzy=False, page_size=20
                                ),
                            )
                        timings.append(round((time.perf_counter() - started) * 1000, 2))
                        report["baseline"] = {"total": result["total"], "durations_ms": timings}
                    except Exception as error:
                        report["baseline"] = {
                            "error": "content_search_limit"
                            if getattr(error, "status_code", None) == 422
                            else type(error).__name__
                        }
                        break
            reports.append(report)
            print(
                json.dumps(
                    {
                        "documents": size,
                        "queries": report["queries"],
                        "baseline": report.get("baseline"),
                    }
                ),
                flush=True,
            )
        gc.collect()
        tracemalloc.start()
        memory = []
        for _ in range(15):
            measured(factory, actor, SearchInput(query="содержание территорий", fuzzy=False), 1)
            gc.collect()
            memory.append(tracemalloc.get_traced_memory()[0])
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        output = {
            "algorithm": "lexical-2",
            "synthetic_only": True,
            "file_stride": args.file_stride,
            "file_count": math.ceil(args.sizes[-1] / args.file_stride),
            "transaction_jit_off": args.jit_off,
            "corpora": reports,
            "memory": {
                "retrieval_peak_python_bytes": peak,
                "steady_first_bytes": memory[4],
                "steady_last_bytes": memory[-1],
                "process_peak_rss_kib_including_seed": resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss,
            },
            "connections_checked_out_after_requests": target.pool.checkedout(),
        }
        Path(args.output).write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    finally:
        target.dispose()
        if created:
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
        admin.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes", type=lambda s: [int(v) for v in s.split(",")], default=[1000, 10000, 150001]
    )
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--baseline")
    parser.add_argument("--jit-off", action="store_true")
    parser.add_argument("--file-stride", type=int, choices=range(1, 1001), default=1000)
    parser.add_argument("--output", required=True)
    run(parser.parse_args())
