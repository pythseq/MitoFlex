import os
import sys
from os import path

try:
    sys.path.insert(0, os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..")))
    from utility.helper import shell_call
    from misc.check_circular import check_circular
    from utility import logger
    from Bio import SeqIO
except ImportError as err:
    sys.exit(
        f"Unable to import helper module {err.name}, is the installation of MitoFlex valid?")

bin_dir = path.dirname(__file__)
soap_fusion = path.join(bin_dir, 'SOAPdenovo-fusion')
soap_127 = path.join(bin_dir, 'SOAPdenovo-127mer')


class SOAP():

    def __init__(self, fq1, fq2, contigs, read_length, insert_size, basedir, threads, prefix):
        self.fq1 = path.abspath(fq1)
        self.fq2 = path.abspath(fq2)
        self.contigs = path.abspath(contigs)
        self.insert_size = insert_size
        self.lib_file = None
        self.basedir = path.join(path.abspath(basedir), f"{prefix}.scaf")
        self.read_length = read_length
        self.threads = threads
        os.mkdir(self.basedir)

    def lib(self):
        avg_ins = self.insert_size
        self.lib_file = path.join(self.basedir, "soaplib.txt")
        with open(self.lib_file, 'w') as f:
            f.write(
                f'max_rd_len={self.read_length}\n'
                '[LIB]\n'
                f'avg_ins={avg_ins}\n'
                'reverse_seq=0\n'
                'asm_flags=3\n'
                'rank=1\n'
                'pair_num_cutoff=3\n'
                'map_len=32\n'
                f'q1={self.fq1}\n' + f"q2={self.fq2}" if self.fq2 != None else "")

    def scaf(self):
        if self.lib_file == None:
            raise RuntimeError("Lib was not build before scaffolding!")

        kmer = int(self.read_length / 2)
        prefix = path.join(self.basedir, f'k{kmer}')

        # Prepare
        logger.log(2, "Constructing graph for SOAPdenovo-127.")
        shell_call(soap_fusion, D=True, s=self.lib_file,
                   p=self.threads, K=kmer, g=prefix, c=self.contigs)

        # Map
        logger.log(2, "Mapping sequences.")
        shell_call(soap_127, 'map', s=self.lib_file,
                   p=self.threads, g=prefix)

        # Scaff
        logger.log(2, "Scaffolding.")
        shell_call(soap_127, 'scaff', p=self.threads, g=prefix)

        scaf2mega(prefix + '.scafSeq', prefix + '.fasta')

        return prefix + '.fasta'


def scaf2mega(i, o):
    translated = []
    results = check_circular(1000, 300, 300, i)

    for idx, s in enumerate(SeqIO.parse(i, 'fasta')):
        multi = s.description.split()[1]
        flag = 3 if results and len(results) >= idx and \
            results[idx][0] != -1 else 1
        s.description = f"flag={flag} multi={multi} len={len(s)}"
        translated.append(s)

    SeqIO.write(translated, o, 'fasta')