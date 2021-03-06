"""
annotation_toolkit.py
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
from os import path
import sys
import subprocess
import multiprocessing
from itertools import tee, chain

from pandas.core.frame import DataFrame

try:
    sys.path.insert(0, os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..")))
    from utility.helper import concat_command, direct_call, shell_call
    from utility import logger
    from utility.bio import wuss, infernal
    import pandas
    import numpy as np
    from Bio import SeqIO
    from Bio.Seq import Seq
    from Bio.SeqRecord import SeqRecord
    from Bio.Data import CodonTable
    import configurations
except ImportError as err:
    sys.exit(
        f"Unable to import helper module {err.name}, is the installation of MitoFlex valid?")


# Truncates all the -- to - to suit blast's parsing style
def truncated_call(*args, **kwargs):
    return direct_call(concat_command(*args, **kwargs).replace('--', '-'))


# Use multiprocessing to actually boost the search
def tblastn_multi(dbfile=None, infile=None, genetic_code=9, basedir=None,
                  prefix=None, threads=8):

    infile = path.abspath(infile)
    dbfile = path.abspath(dbfile)

    truncated_call('makeblastdb', '-in', infile, dbtype='nucl')

    tasks = []

    protein_data_dir = path.join(basedir, 'tblastn_data')

    try:
        os.mkdir(protein_data_dir)
    except FileExistsError:
        raise RuntimeError(
            "Folder is already created, please make sure the working folder is clean.")

    logger.log(1, f'Making {threads} small datasets for calling tblastn.')
    tblastn_db = np.array_split(list(SeqIO.parse(dbfile, 'fasta')), threads)
    for idx, data in enumerate(tblastn_db):
        if data.any():
            logger.log(0, f'Dataset {idx} has {len(data)} queries.')
            dataset_path = path.join(protein_data_dir, f'dataset_{idx}.fasta')
            SeqIO.write(data, dataset_path, 'fasta')
            tasks.append(
                f'tblastn -evalue 1e-5 -outfmt 6 -seg no -db_gencode {genetic_code} -db {infile} -query {dataset_path}')
    logger.log(1, f'Generating map for calling tblastn.')
    pool = multiprocessing.Pool(processes=threads)

    out_blast = path.join(path.abspath(basedir), f'{prefix}.blast')
    with open(out_blast, 'w') as f:
        pool.map_async(direct_call, tasks, callback=lambda x: f.write(''.join(x)))
        logger.log(1, f'Waiting for all processes to finish.')
        pool.close()
        pool.join()

    logger.log(1, f'Cleaning generated temp files.')
    shell_call('rm -r', protein_data_dir)
    os.remove(f'{infile}.nhr')
    os.remove(f'{infile}.nin')
    os.remove(f'{infile}.nsq')
    return out_blast


def blastn_multi(dbfile=None, infile=None, basedir=None, prefix=None, threads=8):
    infile = path.abspath(infile)
    dbfile = path.abspath(dbfile)

    truncated_call('makeblastdb', '-in', infile, dbtype='nucl')

    nucl_data_dir = path.join(basedir, "blastn_data")

    try:
        os.mkdir(nucl_data_dir)
    except FileExistsError:
        raise RuntimeError("Folder is already created, please make sure the working folder is clean.")

    logger.log(1, f'Making {threads} small datasets for calling blastn.')

    file_names = [path.join(nucl_data_dir, f'dataset_{x}.fasta') for x in range(threads)]

    tasks = [f'blastn -evalue 1e-5 -outfmt 6 -db {infile} -query {dataset_path}' for dataset_path in file_names]
    seqs = [[] for i in range(threads)]

    for i, seq in enumerate(SeqIO.parse(dbfile, 'fasta')):
        seqs[i % threads].append(seq)

    for i in range(threads):
        SeqIO.write(seqs[i], file_names[i], 'fasta')

    logger.log(1, 'Generating map for calling blastn.')
    pool = multiprocessing.Pool(processes=threads)

    out_blast = path.join(path.abspath(basedir), f'{prefix}.blast')
    with open(out_blast, 'w') as f:
        pool.map_async(direct_call, tasks, callback=lambda x: f.write(''.join(x)))
        pool.close()
        logger.log(1, "Waiting for all processes to finish.")
        pool.join()

    logger.log(1, f'Cleaning generated temp files.')

    shell_call('rm -r', nucl_data_dir)
    os.remove(f'{infile}.nhr')
    os.remove(f'{infile}.nin')
    os.remove(f'{infile}.nsq')

    return out_blast


def blast_to_csv(blast_file, ident=30, score=25):
    blast_frame = pandas.read_csv(
        blast_file, delimiter='\t',
        names=['qseq', 'sseq', 'ident',
               'length', 'mismatch', 'gap',
               'qstart', 'qend', 'sstart',
               'send', 'evalue', 'score'])

    # Delete duplicated and unqualified results, then add extra informations about results.
    blast_frame = blast_frame.drop_duplicates(keep='first')
    blast_frame = blast_frame[blast_frame.ident > ident]
    blast_frame = blast_frame[blast_frame.score > score]
    blast_frame['qmax'] = blast_frame.groupby('qseq')['qend'].transform(
        lambda x: max(x) if x.count() > 2 else x)
    blast_frame = blast_frame[blast_frame.qend - blast_frame.qstart
                              >= blast_frame.qmax * 0.25]

    # For logging purpose
    os.remove(blast_file)
    out_blast_csv = f'{blast_file}.csv'
    blast_frame.to_csv(out_blast_csv, index=False)

    return blast_frame, out_blast_csv


# Filter out the most important sequences
def wash_blast_results(blast_frame: pandas.DataFrame = None, mut_plus=True):
    cutoff = configurations.annotation.overlap_ratio
    if mut_plus:
        blast_frame['plus'] = (blast_frame.send - blast_frame.sstart) > 0
    blast_frame['sstart'], blast_frame['send'] = np.where(
        blast_frame['sstart'] > blast_frame['send'],
        [blast_frame['send'], blast_frame['sstart']],
        [blast_frame['sstart'], blast_frame['send']]
    )

    by_sseq = dict(tuple(blast_frame.groupby(['sseq'])))

    by_sseq = {key: value.sort_values('sstart')
               for key, value in by_sseq.items()}

    results = []

    for frame in by_sseq.values():
        # Find all highest score results which does not overlap with
        # any other sequences.
        while not frame.empty:
            highest = frame[frame.score == frame.score.max()].head(1)
            results.append(highest)

            max_len = int(highest.send - highest.sstart) + 1
            max_start = int(highest.sstart) + 1
            max_end = int(highest.send)
            max_gene = str(highest.qseq).split('_')[3]

            frame = frame.drop(highest.index)

            # Apply a conflict check for genes
            # If the gene overlapping is equal to the highest, set
            # the overlapping cutoff to 0 (No tolerance).
            # The check is used for situations where multiple queries
            # overlapped, but they all have a same PCG, which is
            # hard to detect the border of the single gene.
            conf = ~frame.qseq.str.contains(max_gene)
            conf = conf.map(lambda x: max_len if x else 0)

            cutoffs = np.minimum(max_len, frame.send - frame.sstart)
            cutoffs = np.minimum(cutoffs, conf) * cutoff

            overlays = np.minimum(frame.send, max_end) - \
                np.maximum(frame.sstart, max_start)
            frame = frame[overlays <= cutoffs]

    return pandas.concat(results)


# Call genewise
def genewise(basedir=None, prefix=None, codon_table=None,
             wises: pandas.DataFrame = None, infile=None,
             dbfile=None, cutoff=0.5):
    wise_dir = path.abspath(
        path.join(path.dirname(__file__), '..', 'profile', 'genewise'))

    if codon_table is None:
        codon_table = path.join(wise_dir, 'codon_InverMito.table')

    wisedir = path.join(basedir, 'genewise')
    dbdir = path.join(wisedir, 'sequences')
    query_dir = path.join(wisedir, 'queries')
    try:
        os.makedirs(wisedir, exist_ok=True)
        os.makedirs(dbdir, exist_ok=True)
        os.makedirs(query_dir, exist_ok=True)
    except Exception:
        raise RuntimeError('Cannot validate folders for genewise, exiting.')

    queries = {record.id: record
               for record in SeqIO.parse(infile, 'fasta')
               if record.id in set(wises.sseq)}

    dbparsed = {record.id: record
                for record in SeqIO.parse(dbfile, 'fasta')
                if record.id in set(wises.qseq)}

    for idx, record in dbparsed.items():
        SeqIO.write(record, path.join(dbdir, f'{idx}.fa'), 'fasta')

    wise_cfg_dir = path.join(wise_dir, 'wisecfg')
    env_var = dict(os.environ)
    env_var["WISECONFIGDIR"] = wise_cfg_dir

    wises = wises.assign(wise_cover=np.nan, wise_shift=np.nan,
                         wise_min_start=np.nan, wise_max_end=np.nan)

    for index, wise in wises.iterrows():
        # Extending search region for more sensitive finding
        extended_sstart = wise.sstart - 30 if wise.sstart > 30 else 0
        extended_send = min(wise.send + 30, len(queries[wise.sseq]))

        query_prefix = f'{wise.qseq}_{wise.sseq}_{extended_sstart}_{extended_send}'
        query_file = path.join(query_dir, f'{query_prefix}.fa')

        seq = queries[wise.sseq]
        seq.id = f'{wise.qseq}_{wise.sseq}_{extended_send}_{extended_send}'

        SeqIO.write(queries[wise.sseq]
                    [extended_sstart:extended_send], query_file, 'fasta')

        result = subprocess.check_output(
            concat_command('genewise', codon=codon_table,
                           trev=not wise.plus, genesf=True, gff=True, sum=True,
                           appending=[
                               path.join(dbdir, f'{wise.qseq}.fa'), query_file]
                           ).replace("--", '-'), env=env_var, shell=True).decode('utf-8')
        with open(path.join(basedir, 'genewise.txt'), 'a') as fgw:
            print(result, file=fgw)

        # Parse the results
        splited = result.split('//\n')
        info = splited[0].split('\n')[1].split()

        wise_cover = float(
            int(info[3]) - int(info[2]) + 1) / len(dbparsed[str(wise.qseq)])

        wise_result = [x.split('\t')
                       for x in splited[2].split('\n')[:-1]
                       if x.split('\t')[2] == 'cds']
        for x in wise_result:
            # Fix the actual position of seq
            start, end = int(x[3]) + extended_sstart - 1, int(x[4]) + extended_sstart - 1
            x[3] = min(start, end)
            x[4] = max(start, end)
        wise_result.sort(key=lambda x: x[3])
        wise_shift = sum(x[2] == 'match' for x in wise_result) - 1
        wise_start = min(x[3] for x in wise_result)
        wise_end = max(x[4] for x in wise_result)

        pandas.set_option('mode.chained_assignment', None)
        wises['wise_cover'][index] = wise_cover
        wises['wise_shift'][index] = wise_shift
        wises['wise_min_start'][index] = wise_start if wise.plus else wise_end
        wises['wise_max_end'][index] = wise_end if wise.plus else wise_start

    wises.to_csv(path.join(basedir, f'{prefix}.wise.csv'), index=False)
    return wises, queries, dbparsed


def reloc_genes(fasta_file=None, wises: pandas.DataFrame = None, code=9):
    wise_seqs = {x.id: x for x in SeqIO.parse(fasta_file, 'fasta')}
    wises.assign(start_real=np.nan, end_real=np.nan)
    for _, wise in wises.iterrows():
        start_real = end_real = -1
        seq = wise_seqs[wise.sseq][wise.sstart - 29: wise.send + 30]
        if not wise.plus:
            seq = seq.reverse_complement()

        # Truncating sequence to prevent future error of Biopython
        if len(seq) % 3 != 0:
            seq = seq[:-(len(seq) % 3)]

        try:
            trans = seq.translate(9, cds=True)
        except Exception:
            trans = seq.translate(9)
        # Finding stop
        if trans.seq.find('*') != -1:
            offset = trans.seq.find('*') * 3
            end_real = (wise.sstart + 1 + offset
                        if wise.plus else
                        wise.send - offset)
        else:
            mercy = seq[-30:] if wise.plus else seq[:30].reverse_complement()
            offset = mercy.seq.find('TA')
            if offset == -1:
                offset = mercy.seq.find('T')

            if offset != -1:
                end_real = (
                    wise.send + 30 + offset if wise.plus else wise.sstart - 28 - offset)

        # Finding start
        # Find the last M of mercied part of translated seq, where it should be the start codon
        mercy = trans[:10:-1].seq.find('M')
        if mercy != -1:
            offset = (10 - mercy) * 3
            start_real = wise.sstart + offset - 29 if wise.plus else wise.send + 30 + offset

        wise.wise_min_start, wise.wise_max_end = (
            start_real, end_real) if wise.plus else (end_real, start_real)

    return wises


def redirect_genome(fasta_file=None, blast_frame: pandas.DataFrame = None):
    reblast = False

    def redirection(seq: SeqRecord):
        nonlocal reblast
        partial_frame = blast_frame[blast_frame.qseq == seq.id]

        negative = len(partial_frame[partial_frame.sstart > partial_frame.send]) >= len(partial_frame) / 2
        if negative:
            reblast = True
        return seq.reverse_complement(id=True, name=True, description=True) if negative else seq

    SeqIO.write([x for x in map(redirection, SeqIO.parse(fasta_file, 'fasta'))], fasta_file, 'fasta')

    return reblast


def trna_search(fasta_file=None, profile_dir=None, basedir=None, prefix=None, gene_code=9, e_value=0.001, overlap_cutoff=40):
    # Make sure it's the absolute path
    fasta_file = path.abspath(fasta_file)
    profile_dir = path.abspath(profile_dir)
    basedir = path.abspath(basedir)

    codon_table = CodonTable.generic_by_id[gene_code]
    forward_table = codon_table.forward_table

    infernal_file = path.join(basedir, f'{prefix}.infernal.out')

    query_results = []
    for idx, cm in enumerate(os.listdir(profile_dir)):
        indexed = f'{infernal_file}.{idx}'
        truncated_call('cmsearch', E=e_value, o=indexed, appending=[
                       path.join(profile_dir, cm), fasta_file])
        query_results.append(infernal.Infernal(indexed))

    gene_map = []

    for result in query_results:
        for align in result.alignments:
            loop = align.alignment

            # Get the main loop of tRNA
            main = [x for x in loop.components if isinstance(
                x, wuss.MultiLoop)]
            if not main:
                continue
            main = main[0]

            # Get the three hairpin loops of the main loop
            hairpins = [x for x in main.components if isinstance(
                x, wuss.HairpinLoop)]
            if len(hairpins) < 2:
                continue

            # Get the center hairpin loop (anticodon arm)
            # No gap is allowed
            center = hairpins[1]
            if len(center.hairpin.sequence) != 7:
                continue

            # Can't read the center tri-base codon
            if '-' in center.hairpin.to_str()[2:5]:
                logger.log(
                    1, f'Unqualified fold discarded, central hairpin : {center.hairpin.to_str()}, sequence : {center.sequence}')
                continue

            code = Seq(center.hairpin.to_str()[2:5]).reverse_complement()
            amino = forward_table[code]

            align.amino = amino
            align.length = max(align.seqfrom, align.seqto) - \
                min(align.seqfrom, align.seqto)
            gene_map.append((align.seqfrom, align))
            gene_map.append((align.seqto, align))

    gene_map.sort(key=lambda x: x[0])

    gene_map = [x[1] for x in gene_map]

    # Then find the most possible of all
    def overlapped(mapping: list):
        def pairwise(iterable):
            a, b = tee(iterable)
            next(b, None)
            return zip(a, b)

        for gene_loc, pair_loc in pairwise(mapping):
            dist = max(gene_loc.seqfrom, gene_loc.seqto) - \
                min(pair_loc.seqfrom, pair_loc.seqto)
            if gene_loc != pair_loc and dist >= overlap_cutoff and (dist <= gene_loc.length or dist <= pair_loc.length):
                if gene_loc.score >= pair_loc.score:
                    logger.log(
                        0, f'conflict of {gene_loc.amino} and {pair_loc.amino}, removing {pair_loc.amino}, score:{gene_loc.score}, {pair_loc.score}, overlapping : {dist}')
                    while pair_loc in mapping:
                        mapping.remove(pair_loc)
                else:
                    logger.log(
                        0, f'conflict of {gene_loc.amino} and {pair_loc.amino}, removing {gene_loc.amino}, score:{gene_loc.score}, {pair_loc.score}, overlapping : {dist}')
                    while gene_loc in mapping:
                        mapping.remove(gene_loc)
                return True
        return False

    while overlapped(gene_map):
        pass

    gene_map = list(set(gene_map))

    # Normalize the results
    query_dict = {}
    for gene in gene_map:
        if gene.amino not in query_dict:
            query_dict[gene.amino] = gene
        else:
            query_dict[gene.amino + str(sum(x.startswith(gene.amino)
                                            for x in query_dict.keys()) + 1)] = gene

    missing_trnas = [
        x for x in codon_table.back_table if x not in query_dict and x]
    return query_dict, missing_trnas


def rrna_search(fasta_file=None, profile_dir=None, basedir=None, prefix=None, e_value=None):
    # Just make sure.
    fasta_file = path.abspath(fasta_file)
    profile_dir = path.abspath(profile_dir)
    basedir = path.abspath(basedir)

    # Defined 12s and 16s cm file. Changed if needed.
    cm_12s = path.join(profile_dir, '12s.cm')
    cm_16s = path.join(profile_dir, '16s.cm')

    query_12 = path.join(basedir, '12s.out')
    query_16 = path.join(basedir, '16s.out')

    truncated_call('cmsearch', E=e_value, o=query_12,
                   appending=[cm_12s, fasta_file])
    truncated_call('cmsearch', E=e_value, o=query_16,
                   appending=[cm_16s, fasta_file])

    result_12 = infernal.Queries(query_12)
    result_16 = infernal.Queries(query_16)

    return (result_12.queries[0] if result_12.queries else None,
            result_16.queries[0] if result_16.queries else None)


def nhmmer_search(fasta_file=None, thread_number=None, nhmmer_profile=None,
                  prefix=None, basedir=None):

    logger.log(1, 'Calling nhmmer.')

    # Call nhmmer
    hmm_out = os.path.join(basedir, f'{prefix}.nhmmer.out')
    hmm_tbl = os.path.join(basedir, f'{prefix}.nhmmer.tblout')
    logger.log(1, f'Out file : o={hmm_out}, tbl={hmm_tbl}')
    shell_call('nhmmer', o=hmm_out, tblout=hmm_tbl,
               cpu=thread_number, appending=[nhmmer_profile, fasta_file])

    # Process data to pandas readable table
    hmm_tbl_pd = f'{hmm_tbl}.readable'
    with open(hmm_tbl, 'r') as fin, open(hmm_tbl_pd, 'w') as fout:
        for line in fin:
            striped = line.strip()
            splitted = striped.split()
            # Dispose the description of genes, god damned nhmmer...
            print(' '.join(splitted[:15]), file=fout)

    # Read table with pandas
    hmm_frame = pandas.read_csv(hmm_tbl_pd, comment='#', delimiter=' ',
                                names=[
                                    'target', 'accession1', 'query',
                                    'accession2', 'hmmfrom', 'hmm to',
                                    'alifrom', 'alito', 'envfrom', 'envto',
                                    'sqlen', 'strand', 'e', 'score',
                                    'bias'
                                ])
    hmm_frame = hmm_frame.drop(columns=['accession1', 'accession2'])

    # Deduplicate multiple hits on the same gene of same sequence
    hmm_frame = hmm_frame.drop_duplicates(
        subset=['target', 'query'], keep='first')
    hmm_frame.to_csv(f'{hmm_tbl}.dedup.csv', index=False)

    logger.log(1, f'HMM query have {len(hmm_frame.index)} results.')
    return hmm_frame
