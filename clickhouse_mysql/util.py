#!/usr/bin/env python
# -*- coding: utf-8 -*-


import logging
import pprint
import sys
import importlib.util
import os
from pygelf import GelfUdpHandler

L = logging.getLogger("ClickHouse_migrator")
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
L.addHandler(GelfUdpHandler(host=os.getenv('ELK_HOST'), port=12201, _index=f"lumen-app-{os.getenv('ELK_INDEX_NAME')}",
                            _host=f"lumen-{os.getenv('ELK_INDEX_NAME')}"))


class Util(object):

    @staticmethod
    def join_lists(*args):
        res = []
        for l in args:
            if isinstance(l, list):
                res += l

        return res

    @staticmethod
    def log_row(row, header="log row"):
        log_row = header + "\n"
        if isinstance(row, dict):
            for column, value in row.items():
                log_row += "column: {}={}\n".format(column, value)
        else:
            for value in row:
                log_row += "value: {}\n".format(value)
        L.info(log_row)

    @staticmethod
    def class_from_file(file_name, class_name):
        L.info("sys.path")
        L.info(pprint.pformat(sys.path))
        spec = importlib.util.spec_from_file_location("file_module", file_name)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _class = getattr(module, class_name)
        return _class
