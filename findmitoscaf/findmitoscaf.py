"""
findmitoscaf.py
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
import json
import operator

import pandas
import numpy as np
from Bio import SeqIO
from ete3 import NCBITaxa
from os import path


try:
    sys.path.insert(0, os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..")))
    from utility.helper import shell_call, direct_call, maxs
    from utility.profiler import profiling
    from utility.seq import compile_seq, decompile
    from annotation import annotation_tookit as tk
except Exception as identifier:
    sys.exit("Unable to import helper module, is the installation of MitoFlex valid?")

ncbi = NCBITaxa()
mitoflex_dir = path.abspath(path.join(path.dirname(__file__), '..'))
profile_dir = path.join(mitoflex_dir, 'profile')
profile_dir_hmm = path.join(profile_dir, 'CDS_HMM')
profile_dir_tbn = path.join(profile_dir, 'MT_database')
profile_dir_rna = path.join(profile_dir, 'rRNA_CM')


def get_rank(taxa_name=None):
    name_dict = ncbi.get_name_translator([taxa_name])

    rank_dict = {
        'kindom': 'NA',
        'phylum': 'NA',
        'class': 'NA',
        'order': 'NA',
        'family': 'NA',
        'genus': 'NA',
        'species': 'NA'
    }

    for taxid in ncbi.get_lineage(name_dict[taxa_name][0]):
        rank = ncbi.get_rank(taxid)[taxid]
        taxa = ncbi.get_taxid_translator([taxid])[taxid]
        if rank in rank_dict:
            rank_dict[rank] = taxa

    return rank_dict


@profiling
def findmitoscaf(thread_number=8, clade=None, prefix=None,
                 basedir=None, gene_code=9, taxa=None,
                 contigs_file=None, relaxing=0, multi=10, cover_valve=1, min_multi=3.0):

    # Drop all the sequences where multi is too low to do further analysis
    filtered_fa = f'{prefix}.contigs.filtered.fa'
    filtered_contigs = []
    for seq in SeqIO.parse(contigs_file, 'fasta'):
        trait_string = seq.description.replace(seq.id + ' ', '', 1)
        trait = decompile(trait_string, sep=' ')
        if float(trait['multi']) >= min_multi:
            filtered_contigs.append(seq)
    SeqIO.write(filtered_contigs, filtered_fa, 'fasta')

    # Do nhmmer search and collect, filter results
    nhmmer_profile = path.join(profile_dir_hmm, f'{clade}_CDS.hmm')
    # We use an overall protein dataset to determine what clades diffrent seqs belonged to.
    tbn_profile = path.join(profile_dir_tbn, f'Animal_CDS_protein.fa')

    # do hmmer search
    hmm_frame = nhmmer_search(fasta_file=contigs_file, thread_number=thread_number,
                              nhmmer_profile=nhmmer_profile, prefix=prefix,
                              basedir=basedir)

    # filter by taxanomy
    if taxa is not None:
        _, hmm_frame = filter_taxanomy(
            taxa=taxa, fasta_file=contigs_file, hmm_frame=hmm_frame,
            basedir=basedir, prefix=prefix, dbfile=tbn_profile, gene_code=gene_code,
            relaxing=relaxing)

    contig_data = {x.id: x
                   for x in SeqIO.parse(contigs_file, 'fasta')
                   if x.id in hmm_frame.target}

    # filter by multi
    contig_data_high = []
    contig_data_low = []

    for contig in contig_data:
        if contig.description.startswith(contig.id + ' '):
            contig.description = contig.description.replace(
                contig.id + ' ', '', 1)
        traits = decompile(contig.description, sep=' ')
        if float(traits['multi']) >= multi:
            # Append traits to avoid parsing again
            contig_data_high.append((contig, traits))
        else:
            contig_data_low.append(contig)
            # Here we dispose all the low abundance contigs,
            # so only hmm_frame and contigs_file_high will be used.
            hmm_frame = hmm_frame[hmm_frame.target != contig.id]

    contigs_file_high = path.join(basedir, f'{prefix}.abundance.high.fa')
    contigs_file_low = path.join(basedir, f'{prefix}.abundance.low.fa')

    SeqIO.write([x[0] for x in contig_data_high], contigs_file_high, 'fasta')
    SeqIO.write(contig_data_low, contigs_file_low, 'fasta')

    # scoring by frame
    # Using structure:
    # id:{
    #   score: int,
    #   details:[
    #     'cox1':[
    #       1,      //cover
    #       1       //integrity
    # ]
    # ]
    # }
    with open(path.join(profile_dir_hmm, 'lengths.json')) as d:
        length_json = json.load(d)
        length_clade = length_json[clade]

    for seqdata, trait in contig_data_high:

        # Switch to all the hmm result with the same id as sequence data
        target_frame = hmm_frame[hmm_frame.target == seqdata.id]

        # alifrom=target from, alito=target to, hmmfrom=query from, hmmto=query to
        # reformat the alignment structure for more calculation.

        target_frame['plus'] = target_frame.alito - target_frame.alifrom > 0
        target_frame['alifrom'], target_frame['alito'] = np.where(
            target_frame['alifrom'] > target_frame['alito'],
            [target_frame['alito'], target_frame['alifrom']],
            [target_frame['alifrom'], target_frame['alito']]
        )


@profiling
def nhmmer_search(fasta_file=None, thread_number=None, nhmmer_profile=None,
                  prefix=None, basedir=None):

    # Call nhmmer
    hmm_out = os.path.join(basedir, f'{prefix}.nhmmer.out')
    hmm_tbl = os.path.join(basedir, f'{prefix}.nhmmer.tblout')
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
    hmm_frame = pandas.read_table(hmm_tbl_pd, comment='#', delimiter=' ',
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

    return hmm_frame


def filter_taxanomy(taxa=None, fasta_file=None, hmm_frame: pandas.DataFrame = None, basedir=None,
                    prefix=None, dbfile=None, gene_code=9, relaxing=0):

    # Extract sequences from input fasta file according to hmm frame

    seqs = [record
            for record in SeqIO.parse(fasta_file, 'fasta')
            if record.id in set(hmm_frame['target'])
            ]
    if not seqs:
        raise Exception("Parsed fasta file is empty!")

    hmm_fa = path.join(basedir, f'{prefix}.hmm.filtered.fa')
    with open(hmm_fa, 'w') as f:
        SeqIO.write(seqs, f, 'fasta')

    # Do tblastn and genewise
    blast_file = tk.tblastn(dbfile=dbfile, infile=hmm_fa,
                            genetic_code=gene_code, basedir=basedir, prefix=prefix)
    blast_frame, _ = tk.blast_to_csv(blast_file)
    blast_frame = tk.wash_blast_results(blast_frame)
    wise_frame, queries, database = tk.genewise(basedir=basedir, prefix=prefix,
                                                wises=blast_frame, infile=hmm_fa, dbfile=dbfile)
    final_file = path.join(basedir, f'{prefix}.genewise.cds.fa')
    if not tk.collect_result(final_file, wise_frame, queries, database):
        return None, None

    # Filter output sequences from predicted taxanomy class
    required_rank = get_rank(taxa)
    if not required_rank:
        return None, None

    valids = []
    records = SeqIO.parse(final_file, 'fasta')

    rank_matched = {
        'kindom': 0,
        'phylum': 0,
        'class': 0,
        'order': 0,
        'family': 0,
        'genus': 0,
        'species': 0
    }

    for record in records:
        if record.description.startswith(record.id):
            record.description.replace(record.id + " ", '', 1)
        traits = decompile(record.description)
        qseq = traits['qseq'].split('_')
        sseq = traits['sseq']
        qspecies = ' '.join(qseq[4:6])
        if qspecies.endswith('.'):
            qspecies = qspecies.split('.')[0]

        ranks = get_rank(qspecies)
        for key, item in ranks.items():
            if item == required_rank[key] != 'NA':
                rank_matched[key] += 1
        valids.append((qspecies, sseq, ranks, record))

    valids.append((None, None, required_rank))
    valids = list(set(valids))
    valids.sort(key=lambda x: '|'.join(str(i[1] for i in x[2].items())))

    # determine taxanomy class for filtering
    selected_taxa = "NA"
    selected_rank = ""
    rank_list = list(rank_matched)
    reverse_rank_list = rank_list[::-1]
    for rev_rank in reverse_rank_list:
        if rank_matched[rev_rank] >= 1:
            selected_rank = max(0, rank_list.index(rev_rank)-relaxing)
            selected_taxa = required_rank[selected_rank]
            break

    # filter out values that is not met with the taxanomy
    valids = [x
              for x in valids
              if x[2][selected_rank] == selected_taxa]

    # collect all the valid taxanomy results
    sseqs = set([x[1] for x in valids if x[1] is not None])
    seqs = set([x[3] for x in valids])
    filtered_fa = f'{prefix}.taxa.filtered.fa'
    SeqIO.write(seqs, path.join(basedir, filtered_fa), 'fasta')
    filtered_frame = hmm_frame[hmm_frame.target.isin(sseqs)]

    return filtered_fa, filtered_frame
