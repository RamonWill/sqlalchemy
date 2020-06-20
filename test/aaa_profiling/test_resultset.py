import sys

from sqlalchemy import Column
from sqlalchemy import create_engine
from sqlalchemy import Integer
from sqlalchemy import MetaData
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import testing
from sqlalchemy import Unicode
from sqlalchemy.engine.result import LegacyRow
from sqlalchemy.engine.result import Row
from sqlalchemy.testing import AssertsExecutionResults
from sqlalchemy.testing import eq_
from sqlalchemy.testing import fixtures
from sqlalchemy.testing import profiling
from sqlalchemy.util import u


NUM_FIELDS = 10
NUM_RECORDS = 1000

t = t2 = metadata = None


class ResultSetTest(fixtures.TestBase, AssertsExecutionResults):
    __backend__ = True

    @classmethod
    def setup_class(cls):
        global t, t2, metadata
        metadata = MetaData(testing.db)
        t = Table(
            "table1",
            metadata,
            *[
                Column("field%d" % fnum, String(50))
                for fnum in range(NUM_FIELDS)
            ]
        )
        t2 = Table(
            "table2",
            metadata,
            *[
                Column("field%d" % fnum, Unicode(50))
                for fnum in range(NUM_FIELDS)
            ]
        )

    def setup(self):
        metadata.create_all()
        t.insert().execute(
            [
                dict(
                    ("field%d" % fnum, u("value%d" % fnum))
                    for fnum in range(NUM_FIELDS)
                )
                for r_num in range(NUM_RECORDS)
            ]
        )
        t2.insert().execute(
            [
                dict(
                    ("field%d" % fnum, u("value%d" % fnum))
                    for fnum in range(NUM_FIELDS)
                )
                for r_num in range(NUM_RECORDS)
            ]
        )

        # warm up type caches
        with testing.db.connect() as conn:
            conn.execute(t.select()).fetchall()
            conn.execute(t2.select()).fetchall()
            conn.exec_driver_sql(
                "SELECT %s FROM table1"
                % (", ".join("field%d" % fnum for fnum in range(NUM_FIELDS)))
            ).fetchall()
            conn.exec_driver_sql(
                "SELECT %s FROM table2"
                % (", ".join("field%d" % fnum for fnum in range(NUM_FIELDS)))
            ).fetchall()

    def teardown(self):
        metadata.drop_all()

    @profiling.function_call_count()
    def test_string(self):
        [tuple(row) for row in t.select().execute().fetchall()]

    @profiling.function_call_count()
    def test_unicode(self):
        [tuple(row) for row in t2.select().execute().fetchall()]

    @profiling.function_call_count(variance=0.10)
    def test_raw_string(self):
        stmt = "SELECT %s FROM table1" % (
            ", ".join("field%d" % fnum for fnum in range(NUM_FIELDS))
        )
        with testing.db.connect() as conn:
            [tuple(row) for row in conn.exec_driver_sql(stmt).fetchall()]

    @profiling.function_call_count(variance=0.10)
    def test_raw_unicode(self):
        stmt = "SELECT %s FROM table2" % (
            ", ".join("field%d" % fnum for fnum in range(NUM_FIELDS))
        )
        with testing.db.connect() as conn:
            [tuple(row) for row in conn.exec_driver_sql(stmt).fetchall()]

    def test_contains_doesnt_compile(self):
        row = t.select().execute().first()
        c1 = Column("some column", Integer) + Column(
            "some other column", Integer
        )

        @profiling.function_call_count(variance=0.10)
        def go():
            c1 in row

        go()


class ExecutionTest(fixtures.TestBase):
    __backend__ = True

    def test_minimal_connection_execute(self):
        # create an engine without any instrumentation.
        e = create_engine("sqlite://")
        c = e.connect()
        # ensure initial connect activities complete
        c.exec_driver_sql("select 1")

        @profiling.function_call_count()
        def go():
            c.exec_driver_sql("select 1")

        try:
            go()
        finally:
            c.close()

    def test_minimal_engine_execute(self, variance=0.10):
        # create an engine without any instrumentation.
        e = create_engine("sqlite://")
        # ensure initial connect activities complete

        with e.connect() as conn:
            conn.exec_driver_sql("select 1")

        @profiling.function_call_count()
        def go():
            with e.connect() as conn:
                conn.exec_driver_sql("select 1")

        go()


class RowTest(fixtures.TestBase):
    __requires__ = ("cpython",)
    __backend__ = True

    def _rowproxy_fixture(self, keys, processors, row, row_cls):
        class MockMeta(object):
            def __init__(self):
                pass

            def _warn_for_nonint(self, arg):
                pass

        metadata = MockMeta()

        keymap = {}
        for index, (keyobjs, values) in enumerate(list(zip(keys, row))):
            for key in keyobjs:
                keymap[key] = (index, key)
            keymap[index] = (index, key)
        return row_cls(metadata, processors, keymap, row)

    def _test_getitem_value_refcounts_legacy(self, seq_factory):
        col1, col2 = object(), object()

        def proc1(value):
            return value

        value1, value2 = "x", "y"
        row = self._rowproxy_fixture(
            [(col1, "a"), (col2, "b")],
            [proc1, None],
            seq_factory([value1, value2]),
            LegacyRow,
        )

        v1_refcount = sys.getrefcount(value1)
        v2_refcount = sys.getrefcount(value2)
        for i in range(10):
            row[col1]
            row["a"]
            row[col2]
            row["b"]
            row[0]
            row[1]
            row[0:2]
        eq_(sys.getrefcount(value1), v1_refcount)
        eq_(sys.getrefcount(value2), v2_refcount)

    def _test_getitem_value_refcounts_new(self, seq_factory):
        col1, col2 = object(), object()

        def proc1(value):
            return value

        value1, value2 = "x", "y"
        row = self._rowproxy_fixture(
            [(col1, "a"), (col2, "b")],
            [proc1, None],
            seq_factory([value1, value2]),
            Row,
        )

        v1_refcount = sys.getrefcount(value1)
        v2_refcount = sys.getrefcount(value2)
        for i in range(10):
            row._mapping[col1]
            row._mapping["a"]
            row._mapping[col2]
            row._mapping["b"]
            row[0]
            row[1]
            row[0:2]
        eq_(sys.getrefcount(value1), v1_refcount)
        eq_(sys.getrefcount(value2), v2_refcount)

    def test_value_refcounts_pure_tuple(self):
        self._test_getitem_value_refcounts_legacy(tuple)
        self._test_getitem_value_refcounts_new(tuple)

    def test_value_refcounts_custom_seq(self):
        class CustomSeq(object):
            def __init__(self, data):
                self.data = data

            def __getitem__(self, item):
                return self.data[item]

            def __iter__(self):
                return iter(self.data)

        self._test_getitem_value_refcounts_legacy(CustomSeq)
        self._test_getitem_value_refcounts_new(CustomSeq)
