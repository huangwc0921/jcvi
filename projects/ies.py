#!/usr/bin/env python
# -*- coding: UTF-8 -*-

"""
Locate IES sequences within MIC genome of tetrahymena.
"""

import os.path as op
import sys
import logging

from itertools import groupby

from jcvi.algorithms.formula import outlier_cutoff
from jcvi.formats.bed import Bed, sort, depth, some, mergeBed
from jcvi.formats.base import must_open
from jcvi.utils.counter import Counter
from jcvi.utils.range import Range, range_interleave, range_chain
from jcvi.utils.cbook import percentage
from jcvi.apps.base import OptionParser, ActionDispatcher, need_update, sh


class EndPoint (object):

    def __init__(self, label):
        args = label.split('-')
        self.label = label
        self.leftright = args[0]
        self.position = int(args[1])
        self.reads = int(args[2].strip('r'))


def main():

    actions = (
        ('deletion', 'find IES based on mapping MAC reads'),
        ('insertion', 'find IES excision points based on mapping MIC reads'),
        ('insertionpairs', 'pair up the candidate insertions'),
        ('variation', 'associate IES in parents and progeny'),
            )
    p = ActionDispatcher(actions)
    p.dispatch(globals())


def variation(args):
    """
    %prog variation P1.bed P2.bed F1.bed

    Associate IES in parents and progeny.
    """
    p = OptionParser(variation.__doc__)
    opts, args, iopts = p.set_image_options(args, figsize="5x5")

    if len(args) != 3:
        sys.exit(not p.print_help())

    pfs = [op.basename(x).split('-')[0] for x in args]
    F1 = pfs[-1]
    newbedfile = "-".join(pfs) + ".bed"
    if need_update(args, newbedfile):
        newbed = Bed()
        for pf, filename in zip(pfs, args):
            bed = Bed(filename)
            for b in bed:
                b.accn = "-".join((pf, b.accn))
                b.score = None
                newbed.append(b)
        newbed.print_to_file(newbedfile, sorted=True)

    mergedbedfile = mergeBed(newbedfile, nms=True)
    bed = Bed(mergedbedfile)
    valid = 0
    total_counts = Counter()
    F1_counts = []
    for b in bed:
        accns = b.accn.split(',')
        pfs_accns = [x.split("-")[0] for x in accns]
        pfs_counts = Counter(pfs_accns)
        if len(pfs_counts) != 3:
            continue

        valid += 1
        total_counts += pfs_counts
        F1_counts.append(pfs_counts[F1])

    print >> sys.stderr, \
            "A total of {0} sites show consistent deletions across samples.".\
                    format(percentage(valid, len(bed)))
    for pf, count in total_counts.items():
        print >> sys.stderr, "{0:>9}: {1:.2f} deletions/site".\
                    format(pf, count * 1. / valid)

    F1_counts = Counter(F1_counts)

    from jcvi.graphics.base import plt, savefig

    fig = plt.figure(1, (iopts.w, iopts.h))
    left, height = zip(*sorted(F1_counts.items()))
    for l, h in zip(left, height):
        print >> sys.stderr, "{0:>9} variants: {1}".format(l, h)
        plt.text(l, h + 5, str(h), color="darkslategray", size=8,
                 ha="center", va="bottom", rotation=90)

    plt.bar(left, height, align="center")
    plt.xlabel("Identified number of IES per site")
    plt.ylabel("Count")
    plt.title("IES variation in progeny pool")
    savefig(F1 + ".counts.pdf")


def insertionpairs(args):
    """
    %prog insertionpairs endpoints.bed

    Pair up the candidate endpoints. A candidate exision point would contain
    both left-end (LE) and right-end (RE) within a given distance.

    -----------|   |------------
        -------|   |--------
      ---------|   |----------
            (RE)   (LE)
    """
    p = OptionParser(insertionpairs.__doc__)
    p.add_option("--extend", default=10, type="int",
                 help="Allow insertion sites to match up within distance")
    p.set_outfile()
    opts, args = p.parse_args(args)

    if len(args) != 1:
        sys.exit(not p.print_help())

    bedfile, = args
    mergedbedfile = mergeBed(bedfile, d=opts.extend, nms=True)
    bed = Bed(mergedbedfile)
    fw = must_open(opts.outfile, "w")
    support = lambda x: -x.reads
    for b in bed:
        names = b.accn.split(",")
        ends = [EndPoint(x) for x in names]
        REs = sorted([x for x in ends if x.leftright == "RE"], key=support)
        LEs = sorted([x for x in ends if x.leftright == "LE"], key=support)
        if not (REs and LEs):
            continue
        mRE, mLE = REs[0], LEs[0]
        pRE, pLE = mRE.position, mLE.position
        if pLE < pRE:
            b.start, b.end = pLE - 1, pRE
        else:
            b.start, b.end = pRE - 1, pLE
        b.accn = "{0}|{1}".format(mRE.label, mLE.label)
        b.score = pLE - pRE - 1
        print >> fw, b


def insertion(args):
    """
    %prog insertion mic.mac.bed

    Find IES based on mapping MIC reads to MAC genome. Output a bedfile with
    'lesions' (stack of broken reads) in the MAC genome.
    """
    p = OptionParser(insertion.__doc__)
    p.add_option("--mindepth", default=6, type="int",
                 help="Minimum depth to call an insertion")
    p.set_outfile()
    opts, args = p.parse_args(args)

    if len(args) != 1:
        sys.exit(not p.print_help())

    bedfile, = args
    mindepth = opts.mindepth
    bed = Bed(bedfile)
    fw = must_open(opts.outfile, "w")
    for seqid, feats in bed.sub_beds():
        left_ends = Counter([x.start for x in feats])
        right_ends = Counter([x.end for x in feats])
        selected = []
        for le, count in left_ends.items():
            if count >= mindepth:
                selected.append((seqid, le, "LE-{0}".format(le), count))
        for re, count in right_ends.items():
            if count >= mindepth:
                selected.append((seqid, re, "RE-{0}".format(re), count))
        selected.sort()
        for seqid, pos, label, count in selected:
            label = "{0}-r{1}".format(label, count)
            print >> fw, "\t".join((seqid, str(pos - 1), str(pos), label))


def deletion(args):
    """
    %prog deletion [mac.mic.bam|mac.mic.bed] mic.gaps.bed

    Find IES based on mapping MAC reads to MIC genome.
    """
    p = OptionParser(deletion.__doc__)
    p.add_option("--mindepth", default=3, type="int",
                 help="Minimum depth to call a deletion")
    p.add_option("--minspan", default=30, type="int",
                 help="Minimum span to call a deletion")
    p.add_option("--split", default=False, action="store_true",
                 help="Break at cigar N into separate parts")
    p.set_tmpdir()
    opts, args = p.parse_args(args)

    if len(args) != 2:
        sys.exit(not p.print_help())

    bedfile, gapsbedfile = args
    if bedfile.endswith(".bam"):
        bamfile = bedfile
        bedfile = bamfile.replace(".sorted.", ".").replace(".bam", ".bed")
        if need_update(bamfile, bedfile):
            cmd = "bamToBed -i {0}".format(bamfile)
            if opts.split:
                cmd += " -split"
            cmd += " | cut -f1-4"
            sh(cmd, outfile=bedfile)

    sort_tmpdir = "--tmpdir={0}".format(opts.tmpdir)
    if bedfile.endswith(".sorted.bed"):
        pf = bedfile.rsplit(".", 2)[0]
        sortedbedfile = bedfile
    else:
        pf = bedfile.rsplit(".", 1)[0]
        sortedbedfile = pf + ".sorted.bed"
        if need_update(bedfile, sortedbedfile):
            sort([bedfile, "-u", "--accn", sort_tmpdir])

    # Find reads that contain multiple matches
    ibedfile = pf + ".d.bed"
    if need_update(sortedbedfile, ibedfile):
        bed = Bed(sortedbedfile, sorted=False)
        fw = open(ibedfile, "w")
        logging.debug("Write deletions to `{0}`.".format(ibedfile))
        for accn, bb in groupby(bed, key=lambda x: x.accn):
            bb = list(bb)
            branges = [(x.seqid, x.start, x.end) for x in bb]
            iranges = range_interleave(branges)
            for seqid, start, end in iranges:
                if end - start + 1 < opts.minspan:
                    continue
                print >> fw, "\t".join(str(x) for x in \
                            (seqid, start - 1, end, accn + '-d'))
        fw.close()

    # Uniqify the insertions and count occurrences
    countbedfile = pf + ".uniq.bed"
    if need_update(ibedfile, countbedfile):
        bed = Bed(ibedfile)
        fw = open(countbedfile, "w")
        logging.debug("Write counts to `{0}`.".format(countbedfile))
        registry = Counter((x.seqid, x.start, x.end) for x in bed)
        ies_id = 1
        for (seqid, start, end), count in registry.items():
            ies_name = "{0:05d}-r{1}".format(ies_id, count)
            if count < opts.mindepth:
                continue
            print >> fw, "\t".join(str(x) for x in \
                            (seqid, start - 1, end, ies_name))
            ies_id += 1
        fw.close()
        sort([countbedfile, "-i", sort_tmpdir])

    # Remove deletions that contain some read depth
    depthbedfile = pf + ".depth.bed"
    if need_update((sortedbedfile, countbedfile), depthbedfile):
        depth([sortedbedfile, countbedfile, "--outfile={0}".format(depthbedfile)])

    validbedfile = pf + ".valid.bed"
    if need_update(depthbedfile, validbedfile):
        fw = open(validbedfile, "w")
        logging.debug("Filter valid deletions to `{0}`.".format(validbedfile))
        bed = Bed(depthbedfile)
        all_scores = [float(b.score) for b in bed]
        lb, ub = outlier_cutoff(all_scores)
        logging.debug("Bounds for depths: LB={0:.2f} (ignored)  UB={1:.2f}".format(lb, ub))
        for b in bed:
            if float(b.score) > ub:
                continue
            print >> fw, b
        fw.close()

    # Remove deletions that contain sequencing gaps on its flanks
    selectedbedfile = pf + ".selected.bed"
    if need_update(validbedfile, selectedbedfile):
        flanksbedfile = pf + ".flanks.bed"
        fw = open(flanksbedfile, "w")
        bed = Bed(validbedfile)
        flank = 100
        logging.debug("Write deletion flanks to `{0}`.".format(flanksbedfile))
        for b in bed:
            start, end = b.start, b.end
            b.start, b.end = start, min(start + flank - 1, end)
            print >> fw, b
            b.start, b.end = max(start, end - flank + 1), end
            print >> fw, b
        fw.close()

        intersectidsfile = pf + ".intersect.ids"
        cmd = "intersectBed -a {0} -b {1}".format(flanksbedfile, gapsbedfile)
        cmd += " | cut -f4 | sort -u"
        sh(cmd, outfile=intersectidsfile)
        some([validbedfile, intersectidsfile, "-v",
                "--outfile={0}".format(selectedbedfile)])

    # Find best-scoring non-overlapping set
    iesbedfile = pf + ".ies.bed"
    if need_update(selectedbedfile, iesbedfile):
        bed = Bed(selectedbedfile)
        fw = open(iesbedfile, "w")
        logging.debug("Write IES to `{0}`.".format(iesbedfile))
        branges = [Range(x.seqid, x.start, x.end, int(x.accn.rsplit("r")[-1]), i) \
                        for i, x in enumerate(bed)]
        iranges, iscore = range_chain(branges)
        logging.debug("Best chain score: {0} ({1} IES)".\
                        format(iscore, len(iranges)))
        ies_id = 1
        for seqid, start, end, score, id in iranges:
            ies_name = "IES-{0:05d}-r{1}".format(ies_id, score)
            span = end - start + 1
            print >> fw, "\t".join(str(x) for x in \
                            (seqid, start - 1, end, ies_name, span))
            ies_id += 1
        fw.close()


if __name__ == '__main__':
    main()
