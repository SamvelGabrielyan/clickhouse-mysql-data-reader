#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import os
import time
import sys
import traceback

from pymysqlreplication import BinLogStreamReader
from pymysqlreplication.row_event import WriteRowsEvent, UpdateRowsEvent, DeleteRowsEvent

from clickhouse_mysql.reader.reader import Reader
from clickhouse_mysql.event.event import Event
from clickhouse_mysql.tableprocessor import TableProcessor
from clickhouse_mysql.util import Util
from clickhouse_mysql.util import L
from clickhouse_driver import Client
from clickhouse_mysql.clioptions import AggregatedOptions
# from clickhouse_mysql.config import Config
#from pymysqlreplication.event import QueryEvent, RotateEvent, FormatDescriptionEvent


class MySQLReader(Reader):
    """Read data from MySQL as replication ls"""

    connection_settings = None
    server_id = None
    log_file = None
    log_pos = None
    schemas = None
    tables = None
    tables_prefixes = None
    blocking = None
    resume_stream = None
    binlog_stream = None
    nice_pause = 0

    write_rows_event_num = 0
    write_rows_event_each_row_num = 0

    binlog_position_file = None

    def __init__(
            self,
            connection_settings,
            server_id,
            log_file=None,
            log_pos=None,
            schemas=None,
            tables=None,
            tables_prefixes=None,
            blocking=None,
            resume_stream=None,
            nice_pause=None,
            binlog_position_file=None,
            callbacks={},
    ):
        super().__init__(callbacks=callbacks)

        self.connection_settings = connection_settings
        self.server_id = server_id
        self.log_file = log_file
        self.log_pos = log_pos
        self.schemas = None if not TableProcessor.extract_dbs(schemas, Util.join_lists(tables, tables_prefixes)) else TableProcessor.extract_dbs(schemas, Util.join_lists(tables, tables_prefixes))
        self.tables = None if tables is None else TableProcessor.extract_tables(tables)
        self.tables_prefixes = None if tables_prefixes is None else TableProcessor.extract_tables(tables_prefixes)
        self.blocking = blocking
        self.resume_stream = resume_stream
        self.nice_pause = nice_pause
        self.binlog_position_file=binlog_position_file

        L.info("raw dbs list. len()=%d", 0 if schemas is None else len(schemas))
        if schemas is not None:
            for schema in schemas:
                L.info(schema)
        L.info("normalised dbs list. len()=%d", 0 if self.schemas is None else len(self.schemas))
        if self.schemas is not None:
            for schema in self.schemas:
                L.info(schema)

        L.info("raw tables list. len()=%d", 0 if tables is None else len(tables))
        if tables is not None:
            for table in tables:
                L.info(table)
        L.info("normalised tables list. len()=%d", 0 if self.tables is None else len(self.tables))
        if self.tables is not None:
            for table in self.tables:
                L.info(table)

        L.info("raw tables-prefixes list. len()=%d", 0 if tables_prefixes is None else len(tables_prefixes))
        if tables_prefixes is not None:
            for table in tables_prefixes:
                L.info(table)
        L.info("normalised tables-prefixes list. len()=%d", 0 if self.tables_prefixes is None else len(self.tables_prefixes))
        if self.tables_prefixes is not None:
            for table in self.tables_prefixes:
                L.info(table)

        if not isinstance(self.server_id, int):
            raise Exception("Please specify server_id of src server as int. Ex.: --src-server-id=1")

        self.binlog_stream = BinLogStreamReader(
            # MySQL server - data source
            connection_settings=self.connection_settings,
            server_id=self.server_id,
            # we are interested in reading CH-repeatable events only
            only_events=[
                # Possible events
                #BeginLoadQueryEvent,
                DeleteRowsEvent,
                #ExecuteLoadQueryEvent,
                #FormatDescriptionEvent,
                #GtidEvent,
                #HeartbeatLogEvent,
                #IntvarEvent
                #NotImplementedEvent,
                #QueryEvent,
                #RotateEvent,
                #StopEvent,
                #TableMapEvent,
                UpdateRowsEvent,
                WriteRowsEvent,
                #XidEvent,
            ],
            only_schemas=self.schemas,
            # in case we have any prefixes - this means we need to listen to all tables within specified schemas
            only_tables=self.tables if not self.tables_prefixes else None,
            log_file=self.log_file,
            log_pos=self.log_pos,
            freeze_schema=True,  # If true do not support ALTER TABLE. It's faster.
            blocking=False,
            resume_stream=self.resume_stream,
        )

    def performance_report(self, start, rows_num, rows_num_per_event_min=None, rows_num_per_event_max=None, now=None):
        # log performance report

        if now is None:
            now = time.time()

        window_size = now - start
        if window_size > 0:
            rows_per_sec = rows_num / window_size
            L.info(
                'PERF - %f rows/sec, min(rows/event)=%d max(rows/event)=%d for last %d rows %f sec',
                rows_per_sec,
                rows_num_per_event_min if rows_num_per_event_min is not None else -1,
                rows_num_per_event_max if rows_num_per_event_max is not None else -1,
                rows_num,
                window_size,
            )
        else:
            L.info("PERF - can not calc performance for time size=0")

    def is_table_listened(self, table):
        """
        Check whether table name in either directly listed in tables or starts with prefix listed in tables_prefixes
        :param table: table name
        :return: bool is table listened
        """

        # check direct table name match
        if self.tables:
            if table in self.tables:
                return True

        # check prefixes
        if self.tables_prefixes:
            for prefix in self.tables_prefixes:
                if table.startswith(prefix):
                    # table name starts with prefix list
                    return True

        return False

    first_rows_passed = []
    start_timestamp = 0
    start = 0
    rows_num = 0
    rows_num_since_interim_performance_report = 0
    rows_num_per_event_min = None
    rows_num_per_event_max = None

    def init_read_events(self):
        self.start_timestamp = int(time.time())
        self.first_rows_passed = []

    def init_fetch_loop(self):
        self.start = time.time()

    def stat_init_fetch_loop(self):
        self.rows_num = 0
        self.rows_num_since_interim_performance_report = 0
        self.rows_num_per_event_min = None
        self.rows_num_per_event_max = None

    def stat_close_fetch_loop(self):
        if self.rows_num > 0:
            # we have some rows processed
            now = time.time()
            if now > self.start + 60:
                # and processing was long enough
                self.performance_report(self.start, self.rows_num, now)

    def stat_write_rows_event_calc_rows_num_min_max(self, rows_num_per_event):
        # populate min value
        if (self.rows_num_per_event_min is None) or (rows_num_per_event < self.rows_num_per_event_min):
            self.rows_num_per_event_min = rows_num_per_event

        # populate max value
        if (self.rows_num_per_event_max is None) or (rows_num_per_event > self.rows_num_per_event_max):
            self.rows_num_per_event_max = rows_num_per_event

    def stat_write_rows_event_all_rows(self, mysql_event):
        self.write_rows_event_num += 1
        self.rows_num += len(mysql_event.rows)
        self.rows_num_since_interim_performance_report += len(mysql_event.rows)
        L.debug('WriteRowsEvent #%d rows: %d', self.write_rows_event_num, len(mysql_event.rows))

    def stat_write_rows_event_each_row(self):
        self.write_rows_event_each_row_num += 1
        L.debug('WriteRowsEvent.EachRow #%d', self.write_rows_event_each_row_num)

    def stat_write_rows_event_each_row_for_each_row(self):
        self.rows_num += 1
        self.rows_num_since_interim_performance_report += 1

    def stat_write_rows_event_finalyse(self):
        if self.rows_num_since_interim_performance_report >= 100000:
            # speed report each N rows
            self.performance_report(
                start=self.start,
                rows_num=self.rows_num,
                rows_num_per_event_min=self.rows_num_per_event_min,
                rows_num_per_event_max=self.rows_num_per_event_max,
            )
            self.rows_num_since_interim_performance_report = 0
            self.rows_num_per_event_min = None
            self.rows_num_per_event_max = None

    def process_first_event(self, event):
        if "{}.{}".format(event.schema, event.table) not in self.first_rows_passed:
            Util.log_row(event.first_row(), "first row in replication {}.{}".format(event.schema, event.table))
            self.first_rows_passed.append("{}.{}".format(event.schema, event.table))
        L.info(self.first_rows_passed)

    def process_write_rows_event(self, mysql_event):
        """
        Process specific MySQL event - WriteRowsEvent
        :param mysql_event: WriteRowsEvent instance
        :return:
        """
        new_rows = []
        for row in mysql_event.rows:
            new_row_dict = {'values': {}}
            for k, v in row['values'].items():
                if type(v) == dict or type(v) == list:
                    v = '0'
                new_row_dict['values'][k] = v
            new_rows.append(new_row_dict)
        for i in range(len(new_rows)):
            mysql_event.rows[i] = new_rows[i]

        if self.tables_prefixes:
            # we have prefixes specified
            # need to find whether current event is produced by table in 'looking-into-tables' list
            if not self.is_table_listened(mysql_event.table):
                # this table is not listened
                # processing is over - just skip event
                return

        # statistics
        self.stat_write_rows_event_calc_rows_num_min_max(rows_num_per_event=len(mysql_event.rows))

        if self.subscribers('WriteRowsEvent'):
            # dispatch event to subscribers

            # statistics
            self.stat_write_rows_event_all_rows(mysql_event=mysql_event)

            # dispatch Event
            event = Event()
            event.schema = mysql_event.schema
            event.table = mysql_event.table
            event.pymysqlreplication_event = mysql_event

            self.process_first_event(event=event)
            self.notify('WriteRowsEvent', event=event)

        if self.subscribers('WriteRowsEvent.EachRow'):
            # dispatch event to subscribers

            # statistics
            self.stat_write_rows_event_each_row()

            # dispatch Event per each row
            for row in mysql_event.rows:
                # statistics
                self.stat_write_rows_event_each_row_for_each_row()

                # dispatch Event
                event = Event()
                event.schema = mysql_event.schema
                event.table = mysql_event.table
                event.row = row['values']

                self.process_first_event(event=event)
                self.notify('WriteRowsEvent.EachRow', event=event)

        self.stat_write_rows_event_finalyse()

    def process_update_rows_event(self, mysql_event):
        #C???????? ?????????????????? 1
        options = AggregatedOptions()
        dst_db = options['dst_schema']
        dst_host = options['dst_host']
        dst_user = options['dst_user']
        dst_pass = options['dst_password']
        client = Client(dst_host, database=dst_db, user=dst_user, password=dst_pass)
        for row in mysql_event.rows:
            updated_id = row['before_values']['id']
            what = ''
            for k, v in row['after_values'].items():
                for k1, v1 in row['before_values'].items():
                    if k == k1 and v != v1:
                        v_type = type(v)
                        if v_type == dict or v_type == list:
                            v = '0'
                        if v_type is not int:
                            what += f"{k}='{v}', "
                        else:
                            what += f"{k}={v}, "

            query = f"ALTER TABLE {dst_db}.{mysql_event.table} UPDATE {what[:-2]} where id={updated_id};"
            L.info(query)
            client.execute(query)

        L.info("Update success")


    def process_delete_rows_event(self, mysql_event):
        # C???????? ?????????????????? 2
        options = AggregatedOptions()
        dst_db = options['dst_schema']
        dst_host = options['dst_host']
        dst_user = options['dst_user']
        dst_pass = options['dst_password']
        client = Client(dst_host, database=dst_db, user=dst_user, password=dst_pass)
        for row in mysql_event.rows:
            deleted_id = row['values']['id']
            query = f"ALTER TABLE {dst_db}.{mysql_event.table} DELETE WHERE id={deleted_id};"
            client.execute(query)
            L.info(query)
        L.info("DELETE Success")

    def process_binlog_position(self, file, pos):
        if self.binlog_position_file:
            with open(self.binlog_position_file, "w") as f:
                f.write("{}:{}".format(file, pos))
        # L.debug("Next event binlog pos: {}.{}".format(file, pos))

    def read(self):
        # main function - read data from source

        self.init_read_events()

        # fetch events
        try:
            while True:
                # L.debug('Check events in binlog stream')

                self.init_fetch_loop()

                # statistics
                self.stat_init_fetch_loop()

                try:
                    # L.debug('Pre-start binlog position: ' + self.binlog_stream.log_file + ":" + str(self.binlog_stream.log_pos) if self.binlog_stream.log_pos is not None else "undef")

                    # fetch available events from MySQL
                    for mysql_event in self.binlog_stream:
                        # new event has come
                        # check what to do with it

                        # L.debug('Got Event ' + self.binlog_stream.log_file + ":" + str(self.binlog_stream.log_pos))
                        # process event based on its type
                        if isinstance(mysql_event, WriteRowsEvent):
                            self.process_write_rows_event(mysql_event)
                        elif isinstance(mysql_event, DeleteRowsEvent):
                            self.process_delete_rows_event(mysql_event)
                        elif isinstance(mysql_event, UpdateRowsEvent):
                            self.process_update_rows_event(mysql_event)
                        else:
                            # skip other unhandled events
                            pass

                        # after event processed, we need to handle current binlog position
                        self.process_binlog_position(self.binlog_stream.log_file, self.binlog_stream.log_pos)

                except KeyboardInterrupt:
                    # pass SIGINT further
                    L.info("SIGINT received. Pass it further.")
                    raise
                except Exception as ex:
                    if self.blocking:
                        # we'd like to continue waiting for data
                        # report and continue cycle
                        L.warning("Got an exception, skip it in blocking mode")
                        L.warning(ex)
                    else:
                        # do not continue, report error and exit
                        L.critical("Got an exception, abort it in non-blocking mode")
                        L.critical(ex)
                        sys.exit(1)

                # all events fetched (or none of them available)

                # statistics
                self.stat_close_fetch_loop()

                if not self.blocking:
                    # do not wait for more data - all done
                    break  # while True

                # blocking - wait for more data
                if self.nice_pause > 0:
                    time.sleep(self.nice_pause)

                self.notify('ReaderIdleEvent')

        except KeyboardInterrupt:
            L.info("SIGINT received. Time to exit.")
        except Exception as ex:
            L.warning("Got an exception, handle it")
            L.warning(traceback.print_exc())
            L.warning(ex)

        try:
            self.binlog_stream.close()
        except Exception as ex:
            L.warning("Unable to close binlog stream correctly")
            L.warning(ex)

        end_timestamp = int(time.time())

        L.info('start %d', self.start_timestamp)
        L.info('end %d', end_timestamp)
        L.info('len %d', end_timestamp - self.start_timestamp)

if __name__ == '__main__':
    connection_settings = {
        'host': '127.0.0.1',
        'port': 3306,
        'user': 'reader',
        'passwd': 'qwerty',
    }
    server_id = 1

    reader = Reader(
        connection_settings=connection_settings,
        server_id=server_id,
    )

    reader.read()
