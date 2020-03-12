#!/usr/bin/env python3

"""
ncbi.py
========

Copyright (c) 2019-2020 Li Junyu <2018301050@szu.edu.cn>.

This file is part of MitoFlex.

MitoFlex is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

MitoFlex is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with MitoFlex.  If not, see <http://www.gnu.org/licenses/>.

"""

import os
from os import path
from os.path import getmtime
from datetime import datetime

import sys
if sys.version_info[0] < 3:
    sys.exit('Python 3 must be installed in current environment! Please check if your environment setup (like conda environment) is deactivated or wrong!')

try:
    from ete3 import NCBITaxa
except ModuleNotFoundError as identifier:
    print(
        f'Module {identifier.name} not found! Please check your MitoFlex installation!')
    sys.exit()
except ImportError as identifier:
    print(
        f'Error occured when importing module {identifier.name}! Please check your system, python or package installation!')
    sys.exit()

dump_file = path.join(path.dirname(__file__), 'taxdump.tar.gz')
dump_file = path.abspath(dump_file)
dump_dir = path.dirname(dump_file)
dump_file_old = path.join(dump_dir, 'old.taxdump.tar.gz')

if os.path.isfile(dump_file):
    os.rename(dump_file, dump_file_old)

try:
    ncbi = NCBITaxa()
    ncbi.update_taxonomy_database()
    if os.path.isfile(dump_file_old):
        os.remove(dump_file_old)
except Exception as idd:
    print("Errors occured when fetching data from NCBI database, falling back to the last fetched database.")
    if path.isfile(dump_file):
        ncbi = NCBITaxa(taxdump_file=os.path.abspath(dump_file_old))
        if os.path.isfile(dump_file_old):
            os.rename(dump_file_old, dump_file)
    else:
        print("A taxdump file is not found under installation directory, cannot build NCBI taxanomy database.")
        print("Please manually download it from http://ftp.ncbi.nih.gov/pub/taxonomy/taxdump.tar.gz , and move it to the installation directory.")
