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

import pandas
from Bio import SeqIO, SeqRecord, Seq
from ete3 import NCBITaxa
from os import path


try:
    sys.path.insert(0, os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..")))
    from utility.bio.seq import decompile, compile_seq
    from annotation import annotation_tookit as tk
    from utility import logger
    from configurations import findmitoscaf as f_conf
    from utility.helper import concat_command, direct_call, shell_call
    from misc.check_circular import check_circular
except ImportError as err:
    sys.exit(
        f"Unable to import helper module {err.name}, is the installation of MitoFlex valid?")

ncbi = NCBITaxa()
mitoflex_dir = path.abspath(path.join(path.dirname(__file__), '..'))
profile_dir = path.join(mitoflex_dir, 'profile')
profile_dir_hmm = path.join(profile_dir, 'CDS_HMM')
profile_dir_tbn = path.join(profile_dir, 'MT_database')
profile_dir_rna = path.join(profile_dir, 'rRNA_CM')

rank_list = ['kindom', 'phylum', 'class',
             'order', 'family', 'genus', 'species']


def get_rank(taxa_name=None):
    name_dict = ncbi.get_name_translator([taxa_name])

    if taxa_name not in name_dict:
        # Try to parse the gene name
        taxa_name = taxa_name.split(' ')[0]
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

    if taxa_name in name_dict:
        for taxid in ncbi.get_lineage(name_dict[taxa_name][0]):
            rank = ncbi.get_rank([taxid])[taxid]
            taxa = ncbi.get_taxid_translator([taxid])[taxid]
            if rank in rank_dict:
                rank_dict[rank] = taxa
    else:
        logger.log(
            2, f'Query name {taxa_name} was skipped because no result found in NCBI database.')

    return [(tax_class, tax_id) for tax_class, tax_id in rank_dict.items()]


def findmitoscaf(thread_number=8, clade=None, prefix=None,
                 basedir=None, gene_code=9, taxa=None, max_contig_len=20000,
                 contigs_file=None, relaxing=0, multi=10, merge_method=1, merge_overlapping=50):

    logger.log(2, 'Finding mitochondrial scaffold.')
    if merge_method == 0:
        logger.log(2, f'Merging sequences with global method.')
        logger.log(2, f'Merged {merge_sequences(contigs_file,overlapped_len=merge_overlapping)} sequences.')

    # Update the total profile before the process
    logger.log(1, 'Updating the general protein database.')
    lc = 0
    with open(path.join(profile_dir_tbn, 'Animal.fa'), 'w') as fout:
        for protein_fas in os.listdir(profile_dir_tbn):
            if protein_fas.endswith('.fa') and protein_fas != 'Animal.fa':
                with open(path.join(profile_dir_tbn, protein_fas)) as fin:
                    for line in fin:
                        fout.write(line)
                        lc += 1
    logger.log(1, f'Generation finished with {lc} writes.')

    # Do nhmmer search and collect, filter results
    nhmmer_profile = path.join(profile_dir_hmm, f'{clade}.hmm')
    logger.log(1, f'nhmmer profile : {nhmmer_profile}')

    # do hmmer search
    hmm_frame = tk.nhmmer_search(fasta_file=contigs_file, thread_number=thread_number,
                                 nhmmer_profile=nhmmer_profile, prefix=prefix,
                                 basedir=basedir)

    logger.log(1, f'Generating hmm-filtered fasta.')
    hmm_seqs = [record
                for record in SeqIO.parse(contigs_file, 'fasta')
                if record.id in set(hmm_frame['target'])
                ]
    if not hmm_seqs:
        raise RuntimeError("Parsed fasta file is empty!")

    hmm_fa = path.join(basedir, f'{prefix}.hmm.filtered.fa')
    with open(hmm_fa, 'w') as f:
        SeqIO.write(hmm_seqs, f, 'fasta')

    # filter by taxanomy
    if taxa is not None:
        # We use an overall protein dataset to determine what clades diffrent seqs belonged to.
        tbn_profile = path.join(profile_dir_tbn, f'Animal.fa')
        hmm_frame = filter_taxanomy(
            taxa=taxa, fasta_file=hmm_fa, hmm_frame=hmm_frame,
            basedir=basedir, prefix=prefix, dbfile=tbn_profile, gene_code=gene_code,
            relaxing=relaxing, threads=thread_number)
    else:
        logger.log(
            3, 'Skipping taxanomy filtering because the disable-taxa option is on.')

    contig_data = [x
                   for x in SeqIO.parse(hmm_fa, 'fasta')
                   if hmm_frame.target.str.contains(x.id).any()]

    if not contig_data:
        raise RuntimeError(
            "The result from nhmmer/filter_taxanomy is empty! Please check if the data is unqualified, or a wrong taxanomy class is given!")

    # filter by multi
    contig_data_high = []
    contig_data_low = []
    contig_multis = {}

    for contig in contig_data:
        if contig.description.startswith(contig.id + ' '):
            contig.description = contig.description.replace(
                contig.id + ' ', '', 1)
        traits = decompile(contig.description, sep=' ')
        if float(traits['multi']) >= multi:
            # Append traits to avoid parsing again
            contig_data_high.append(contig)
            contig_multis[contig.id] = float(traits['multi'])
        else:
            contig_data_low.append(contig)
            # Here we dispose all the low abundance contigs,
            # so only hmm_frame and contigs_file_high will be used.
            hmm_frame = hmm_frame[hmm_frame.target != contig.id]

    contigs_file_high = path.join(basedir, f'{prefix}.abundance.high.fa')
    contigs_file_low = path.join(basedir, f'{prefix}.abundance.low.fa')

    high = SeqIO.write(contig_data_high, contigs_file_high, 'fasta')
    low = SeqIO.write(contig_data_low, contigs_file_low, 'fasta')

    logger.log(
        2, f'{high} records of high abundance, {low} records of low abundance was classified with multi value {multi}.')

    contig_map_high = {x.id: x for x in contig_data_high}

    # Here we pick out the last sequences by using a greedy algorithm
    # the brute is deprecated because I found myself didn't realize what
    # I'm really going to do at the time I created it.
    cds_indexes = json.load(
        open(path.join(profile_dir_hmm, 'required_cds.json')))[clade]

    # Collects all the related cds
    candidates = {}
    sequence_completeness = {}

    for _, row in hmm_frame.iterrows():
        query = str(row.query)
        index = str(row.target)
        score = int(row.score)
        align_start = int(row.alifrom)
        align_end = int(row.alito)
        align_length = abs(align_start - align_end) + 1
        query_start = int(row.hmmfrom)
        query_to = int(row['hmm to'])

        complete = align_length >= cds_indexes[query] * f_conf.full_ratio

        if not complete:
            missing_length = cds_indexes[query] - align_length
            # Check if the alignment is 'isolated', which means no possiblity to be
            # a gene sliced at side.
            complete = complete or (
                query_start > missing_length and
                len(contig_map_high[index]) - query_to > missing_length
            )

            # If such a gene is 'isolated' and being too short to be a valid alignment,
            # ignores it in the calculation.
            if complete and align_length <= cds_indexes[query] * f_conf.min_valid_ratio:
                logger.log(
                    3, f'Ignoring {query} on {index} since no significant length is aligned : {align_length} / {int(cds_indexes[query] * f_conf.min_valid_ratio)}')
                continue

        if index not in sequence_completeness:
            sequence_completeness[index] = []

        if complete:
            sequence_completeness[index].append(query)

        if index not in candidates:
            candidates[index] = {}

        candidates[index][query] = (
            score * contig_multis[index], query_start, query_to, complete
        )

    flatten_candidates = [(key, value) for key, value in candidates.items()]
    flatten_candidates.sort(key=lambda x: len(x[1]), reverse=True)

    selected_candidates = {x: None for x in cds_indexes}

    # Select as many as possible full pcgs
    # As my point of view, using greedy here just ok.
    fulled_pcgs = []
    for candidate in flatten_candidates:
        index = candidate[0]
        mapping = candidate[1]

        completed = [x for x in mapping if mapping[x][3]]
        incompleted = [x for x in mapping if not mapping[x][3]]

        if any([selected_candidates[c] is not None for c in completed]):
            continue
        for c in completed:
            selected_candidates[c] = index
            fulled_pcgs.append(c)

        for c in incompleted:
            if selected_candidates[c] == None:
                selected_candidates[c] = [(index, *mapping[c][:-1])]
            elif isinstance(selected_candidates[c], list):
                selected_candidates[c].append((index, *mapping[c][:-1]))

    # For fragments, select non-conflict sequence as much as possible
    conflicts = []
    for empty_pcg in [x for x in selected_candidates if selected_candidates[x] is None or isinstance(selected_candidates[x], list)]:
        for index, mapping in candidates.items():
            # No pcg in this sequence, next sequence
            if empty_pcg not in mapping:
                continue

            # If any full pcg selected in current sequence, discard.
            if any([x in fulled_pcgs for x in sequence_completeness[index]]):
                continue

            if selected_candidates[empty_pcg] is None:
                selected_candidates[empty_pcg] = []
            # Collect all the sequences
            selected_candidates[empty_pcg].append(
                (index, *mapping[empty_pcg][:-1])
            )

        # Convert all fragments to final results
        if isinstance(selected_candidates[empty_pcg], list):
            logger.log(
                3, f'Gene {empty_pcg} is fragmentized, deducing most possible sequences')
            gene_map = []
            for pos in selected_candidates[empty_pcg]:
                gene_map.append((pos[2], (pos[0], pos[1])))
                gene_map.append((pos[3], (pos[0], pos[1])))
            gene_map.sort(key=lambda x: x[0])
            gene_map = [x[1] for x in gene_map]

            def overlapping():
                for i in range(0, len(gene_map) - 1, 2):
                    left = gene_map[i]
                    right = gene_map[i + 1]
                    if left[0] != right[0]:
                        if left[1] < right[1]:
                            gene_map.remove(left)
                            gene_map.remove(left)
                        else:
                            gene_map.remove(right)
                            gene_map.remove(right)
                        conflicts.append((left, right))
                        return True
                return False

            while overlapping():
                pass
            final_candidates = list(
                set([x[0] for x in gene_map])
            )

            selected_candidates[empty_pcg] = final_candidates

            total_length = sum([abs(candidates[index][empty_pcg][2] - candidates[index][empty_pcg][1])
                                for index in selected_candidates[empty_pcg]])
            logger.log(
                3, f'Recovered {total_length} bps, ratio {total_length/cds_indexes[empty_pcg]}')
    candidates_json = path.join(basedir, f'{prefix}.candidates.json')
    with open(candidates_json, 'w') as f:
        json.dump(selected_candidates, f, sort_keys=True,
                  indent=4, separators=(', ', ": "))
    selected_ids = []
    for x in selected_candidates.values():
        if x is not None:
            if isinstance(x, list):
                selected_ids += x
            else:
                selected_ids.append(x)

    selected_ids = list(set(selected_ids))
    picked_seq = [seq for seq in contig_data_high if seq.id in selected_ids]

    found_pcgs = [x for x in cds_indexes if selected_candidates[x]]
    missing_pcgs = [x for x in cds_indexes if x not in found_pcgs]

    picked_fasta = path.join(basedir, f'{prefix}.picked.fa')
    SeqIO.write(picked_seq, picked_fasta, 'fasta')
    logger.log(2, f'PCGs found : {found_pcgs}')
    if missing_pcgs:
        logger.log(3, f'Missing PCGs : {missing_pcgs}')
        logger.log(3, f'The missing PCGs may not actually missing, but not detected by the nhmmer search, they may be annotated by tblastn in the annotation module.')

    if merge_method == 1:
        logger.log(2, f"Merging sequences with partial method.")
        logger.log(2, f"Merged {merge_partial(fasta_file=picked_fasta, dbfile=contigs_file, overlapped_len=merge_overlapping)}")
        logger.log(2, f'Launching another findmitoscaf run to filter out non-target sequences.')
        picked_fasta = findmitoscaf(thread_number=thread_number, clade=clade, prefix=prefix,
                                    basedir=basedir, gene_code=gene_code, taxa=taxa,
                                    max_contig_len=max_contig_len, contigs_file=picked_fasta,
                                    relaxing=relaxing, multi=multi, merge_method=2,
                                    merge_overlapping=merge_overlapping)

    # Added a circular checker for scaffolds and meta-scaffolds.
    remark_circular(picked_fasta)

    return picked_fasta


def filter_taxanomy(taxa=None, fasta_file=None, hmm_frame: pandas.DataFrame = None, basedir=None,
                    prefix=None, dbfile=None, gene_code=9, relaxing=0, threads=8):

    logger.log(1, f'Filtering taxanomy with tblastn.')
    # Extract sequences from input fasta file according to hmm frame

    # Do tblastn to search out the possible taxanomy of the gene
    blast_file = tk.tblastn_multi(dbfile=dbfile, infile=fasta_file,
                                  genetic_code=gene_code, basedir=basedir, prefix=prefix, threads=threads)
    blast_frame_unfiltered, _ = tk.blast_to_csv(blast_file)
    blast_frame = tk.wash_blast_results(blast_frame_unfiltered)

    # Drop the sequences which don't have even a gene related to taxa
    by_seqid = dict(tuple(blast_frame.groupby(['sseq'])))
    to_save = []
    for key, frame in by_seqid.items():
        is_in = False
        for _, row in frame.iterrows():
            qseq = str(row.qseq).split('_')
            taxa_name = ' '.join([qseq[4], qseq[5]])
            taxa_rank = get_rank(taxa_name)
            required_rank = get_rank(taxa)
            required_id = ncbi.get_name_translator([taxa])[taxa][0]
            required_class = ncbi.get_rank([required_id])[required_id]
            required_index = rank_list.index(required_class)
            # Get last index for the matching rank
            matches = [idx
                       for idx, ((tax_id, tax_name), (required_id, required_name))
                       in enumerate(zip(taxa_rank, required_rank))
                       if required_name == tax_name != 'NA']
            matches.append(-1)
            matched_rank = max(matches)
            if matched_rank + relaxing >= required_index:
                is_in = True
                break
        if is_in:
            to_save.append(key)

    filtered_frame = hmm_frame[hmm_frame['target'].isin(to_save)]
    filtered_frame.to_csv(
        path.join(basedir, f'{prefix}.taxa.csv'), index=False)
    logger.log(
        1, f'{len(filtered_frame.index)} records were selected after the taxanomy filtering.')
    return filtered_frame


# Accepts a bunch of external sequences, and treat hit sequences as a valid mitogenome candidate.
# You must be sure that the sequences you provided is firmly oringinated from mitogenome, otherwise
# it could make the result worse.
def filter_external(fasta_file=None, external_fasta=None):

    pass


def merge_sequences(fasta_file=None, overlapped_len=50, search_range=5, threads=8, index=0):
    # Compose sequences that are possibly be overlapped with each others.

    logger.log(1, "Trying to merge candidates that are possibly overlapped.")

    fasta_file = path.abspath(fasta_file)

    while True:
        blast_results = pandas.read_csv(tk.blastn_multi(fasta_file, fasta_file, path.dirname(fasta_file), 'merge', threads=threads), delimiter="\t", names=[
                                        'que', 'subj', 'ide', 'alen', 'mis', 'gap', 'qs', 'qe', 'ss', 'se', 'ev', 's'
                                        ])
        # Overlap Conditions:
        # 1. Not aligning itself
        # 2. One of the sequences can be sticked into the other in a short range
        # 3. Aligned length is long enough
        blast_results = blast_results[blast_results.que != blast_results.subj]
        blast_results = blast_results[((blast_results.ss < search_range) & (blast_results.se < search_range)) |
                                      (blast_results.qs < search_range)]
        blast_results = blast_results[blast_results.alen >= overlapped_len]

        if blast_results.empty:
            break

        done = []
        seqrec = []
        while not blast_results.empty:
            overlapped = blast_results.iloc[0]
            que, sub = overlapped.que, overlapped.subj
            seq2 = {x.id: x for x in SeqIO.parse(fasta_file, 'fasta') if x.id in [que, sub]}

            qs, qe = overlapped.qs - 1, overlapped.qe
            if overlapped.ss < overlapped.se:
                ss, se = overlapped.ss - 1, overlapped.se
            else:
                ss, se = len(seq2[sub].seq) - overlapped.ss, len(seq2[sub].seq) - (overlapped.se - 1)
                #seq2[sub].seq = seq2[sub].seq[::-1]

            if overlapped.alen >= len(seq2[que]):
                new_seq = Seq.Seq(str(seq2[sub].seq))
            elif overlapped.alen >= len(seq2[sub]):
                new_seq = Seq.Seq(str(seq2[que].seq))
            else:
                if qs > ss:
                    new_seq = Seq.Seq(str(seq2[que].seq[:qe]) + str(seq2[sub].seq[se:]))
                else:
                    new_seq = Seq.Seq(str(seq2[sub].seq[:se]) + str(seq2[que].seq[qe:]))

                if len(new_seq) < len(seq2[que]):
                    new_seq = seq2[que].seq
                elif len(new_seq) < len(seq2[sub]):
                    new_seq = seq2[sub].seq

            logger.log(
                1, f"Overlapped: {que}:({qs},{qe},{len(seq2[que])})&{seq2[sub].id}:({ss},{se},{len(seq2[sub])}) of length {overlapped.alen}, into M{index}:{len(new_seq)}")
            seqrec.append(SeqRecord.SeqRecord(new_seq, id=f"M{index}",
                                              description=f"flag=1 multi=32767 len={len(new_seq)}"))
            index += 1
            done += [que, sub]

            blast_results = blast_results[(blast_results.que != que) & (blast_results.subj != que)]
            blast_results = blast_results[(blast_results.que != sub) & (blast_results.subj != sub)]

        SeqIO.write(seqrec + [x for x in SeqIO.parse(fasta_file, 'fasta') if x.id not in done], open(fasta_file, 'w'), 'fasta')

    return index


def merge_partial(fasta_file=None, dbfile=None, overlapped_len=50, search_range=5, threads=8):
    logger.log(1, 'Trying to merge partial sequences that are possibly overlapped.')

    fasta_file = path.abspath(fasta_file)
    dbfile = path.abspath(dbfile)

    index = 0

    while True:
        # Profile a merging for itself first
        index_merged = merge_sequences(fasta_file, overlapped_len, search_range, threads=threads, index=index)
        modified = index_merged - index > 0
        index = index_merged

        blast_results = pandas.read_csv(tk.blastn_multi(fasta_file, dbfile, path.dirname(fasta_file), 'merge_partial', threads=threads), delimiter="\t", names=[
                                        'que', 'subj', 'ide', 'alen', 'mis', 'gap', 'qs', 'qe', 'ss', 'se', 'ev', 's'
                                        ])

        blast_results = blast_results[blast_results.que != blast_results.subj]
        blast_results = blast_results[((blast_results.ss < search_range) & (blast_results.se < search_range)) |
                                      (blast_results.qs < search_range)]
        blast_results = blast_results[blast_results.alen >= overlapped_len]

        done = []
        seqrec = []

        while not blast_results.empty:
            overlapped = blast_results.iloc[0]
            que, sub = overlapped.que, overlapped.subj
            seq2 = {sub: [x for x in SeqIO.parse(dbfile, 'fasta') if x.id == sub][0],
                    que: [x for x in SeqIO.parse(fasta_file, 'fasta') if x.id == que][0]
                    }

            qs, qe = overlapped.qs - 1, overlapped.qe - 1
            if overlapped.ss < overlapped.se:
                ss, se = overlapped.ss - 1, overlapped.se
            else:
                ss, se = len(seq2[sub].seq) - overlapped.ss, len(seq2[sub].seq) - (overlapped.se - 1)

            if overlapped.alen >= len(seq2[que]):
                new_seq = Seq.Seq(str(seq2[sub].seq))
            elif overlapped.alen >= len(seq2[sub]):
                new_seq = Seq.Seq(str(seq2[que].seq))
            else:
                if qs > ss:
                    new_seq = Seq.Seq(str(seq2[que].seq[:qe]) + str(seq2[sub].seq[se:]))
                else:
                    new_seq = Seq.Seq(str(seq2[sub].seq[:se]) + str(seq2[que].seq[qe:]))

                if len(new_seq) < len(seq2[que]):
                    new_seq = seq2[que].seq
                elif len(new_seq) < len(seq2[sub]):
                    new_seq = seq2[sub].seq

            logger.log(
                1, f"Overlapped: {que}:({qs},{qe},{len(seq2[que])})&{seq2[sub].id}:({ss},{se},{len(seq2[sub])}) of length {overlapped.alen}, into M{index}:{len(new_seq)}")
            seqrec.append(SeqRecord.SeqRecord(new_seq, id=f"M{index}",
                                              description=f"flag=1 multi=32767 len={len(new_seq)}"))
            index += 1
            modified = True

            done += [que, sub]

            blast_results = blast_results[(blast_results.que != que) & (blast_results.subj != que)]
            blast_results = blast_results[(blast_results.que != sub) & (blast_results.subj != sub)]

        if not modified:
            break

        SeqIO.write(seqrec + [x for x in SeqIO.parse(fasta_file, 'fasta') if x.id not in done], open(fasta_file, 'w'), 'fasta')
        SeqIO.write([x for x in SeqIO.parse(dbfile, 'fasta') if x.id not in done], open(dbfile, 'w'), 'fasta')

    return index


def remark_circular(fasta_file=None, overlapped_length=50):
    sequences = [x for x in SeqIO.parse(fasta_file, 'fasta')]
    if len(sequences) > 1:
        return
    overlapping, overlapped, sequence = check_circular(final_fasta=fasta_file)[0]

    if overlapping != -1 and overlapping[0] == 0 and overlapping[1] >= overlapped_length:
        traits = decompile(input_seq=sequence.description, sep=None)
        traits['flag'] = 3
        sequence.description = compile_seq(traits=traits, sep=' ')
        SeqIO.write([sequence], fasta_file, 'fasta')
