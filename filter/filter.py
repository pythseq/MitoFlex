"""
filter.py
=========

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
import sys
try:
    sys.path.insert(0, os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..")))
    from utility.helper import shell_call, direct_call
    from utility.profiler import profiling
except Exception as identifier:
    sys.exit("Unable to import helper module, is the installation of MitoFlex valid?")

filter_dir = os.path.dirname(os.path.abspath(__file__))

@profiling
def filter_se(fqiabs=None, fqoabs=None, Ns=10, quality=55, limit=0.2, start=None, end=None, seq_size=None):
    try:
        shell_call(filter_dir+'/filter_se', i=fqiabs, o=fqoabs,
                   n=Ns, q=quality, l=limit, s=start, e=end, z=seq_size)
    except Exception as identifier:
        print("Error occured when running filter_se!")

    return fqoabs

@profiling
def filter_pe(fq1=None, fq2=None, o1=None, o2=None,
              a1=None, a2=None, dedup=False, mis=3, ali=15,
              start=None, end=None, n=10, q=55, l=0.2, seq_size=None):
    try:
        shell_call(filter_dir+'/filter_pe', _1=fq1,
                   _2=fq2, _3=o1, _4=o2, _5=a1, _6=a2,
                   d=dedup, m=mis, a=ali, s=start,
                   e=end, n=n, q=q, l=l, z=seq_size)
    except Exception as identifier:
        print("Error occured when running filter_pe!")
    return o1, o2
