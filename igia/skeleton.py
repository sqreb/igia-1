#!/usr/bin/env python3
"""
This is a skeleton file that can serve as a starting point for a Python
console script. To run this script uncomment the following line in the
entry_points section in setup.cfg:
    console_scripts =
     igia = igia.skeleton:run

Then run `python setup.py install` which will install the command `igia`.
"""
from igia import __version__
from igia.utils import SeqFile, load_seqinfo, load_txs, load_ann
from igia import GVAR
from igia.linkage import find_linkage
from igia.element import identify_element
from igia.transcript import identify_transcript
import argparse
import sys
import os
import logging
import signal

_logger = logging.getLogger(__name__)

def igia_parser():
    parser = argparse.ArgumentParser(description="Integrative Gene Isoform Assembler")
    parser.add_argument('--version', action='version', version='igia {ver}'.format(ver=__version__))
    parser.add_argument('-v', '--verbose', dest="loglevel", help="set loglevel to INFO", action='store_const',
                        const=logging.INFO)
    parser.add_argument('-vv', '--very-verbose', dest="loglevel", help="set loglevel to DEBUG", action='store_const',
                        const=logging.DEBUG)

    base_group = parser.add_argument_group("Base")
    base_group.add_argument("-o", "--output", type=str, dest="out_dir", metavar="out_dir", required=True,
                            help="Output folder for IGIA assembled transcripts")
    base_group.add_argument("--ngs", nargs="*", type=str, dest="ngs_file", metavar="NGS.bam", required=True,
                            help="Next-generation RNA sequencing data in BAM format")
    base_group.add_argument("--tgs", nargs="*", type=str, dest="tgs_file", metavar="TGS.bam", required=True,
                            help="TGS Long reads (e.g. PacBio) sequencing data in BAM format")

    ext_group = parser.add_argument_group("External")
    ext_group.add_argument("--tss", type=str, dest="tss", metavar="tss.tsv", default="",
                           help=r"TSS information (TAB separated file, Chrom\tSite\tStrand)")
    ext_group.add_argument("--tes", type=str, dest="tes", metavar="tes.tsv", default="",
                           help=r"TES information (TAB separated file, Chrom\tSite\tStrand)")
    ext_group.add_argument("--ann", type=str, dest="ann", metavar="NGS_ann.bed12", default="",
                           help="NGS-based annotation (e.g. TACO) in BED12 format, needs --size for chromsize")
    ext_group.add_argument("--cfm-ann", type=str, dest="cfm_ann", metavar="comfirmed_ann.bed12", default="",
                           help="Comfirmed annotation in BED12 format, needs --size for chromsize")
    ext_group.add_argument("-s", "--size", type=str, dest="size", metavar="chrom.size", default="",
                           help="Chrom size information generated by twoBitInfo")
    ext_group.add_argument("-g", "--genome", type=str, dest="f_genome", metavar="genome.fa", default=None,
                           help="Genome sequence file in FASTA format")

    opt_group = parser.add_argument_group("Options")
    opt_group.add_argument("-r", type=str, dest="rule", metavar="rule", default="single_end",
                           help="NGS library type", choices=["1++,1--,2+-,2-+", "1+-,1-+,2++,2--", "single_end"])
    opt_group.add_argument("--pir", type=float, dest="pir", metavar="pir", default=0.5,
                           help="PIR cutoff for intron retention [default=0.5]")
    opt_group.add_argument("--dtxs", type=int, dest="dtxs", metavar="dtxs", default=500,
                           help="Distance cutoff between two different TSSs/TESs [default=500]")
    opt_group.add_argument("--time-out", type=int, dest="time_out", metavar="time_out", default=None,
                           help="Time out for complex loci")
    opt_group.add_argument("--paraclu-path", type=str, dest="paraclu_path", metavar="paraclu_path", default=None,
                           help="(recommend) Path to paraclu (for detecting TSS and TES from TGS data). If this parameter is set, TGS data will be also used to identify TXS to increase the coverage of gene annotations.")
    return parser

def parse_args(args):
    """Parse command line parameters
    Args:
      args ([str]): command line parameters as list of strings
    Returns:
      :obj:`argparse.Namespace`: command line parameters namespace
    """
    parser = igia_parser()
    return parser.parse_args(args)


def setup_logging(loglevel):
    """Setup basic logging
    Args:
      loglevel (int): minimum loglevel for emitting messages
    """
    logformat = "[%(asctime)s] %(levelname)s:%(name)s:%(message)s"
    logging.basicConfig(level=loglevel, stream=sys.stdout,
        format=logformat, datefmt="%Y-%m-%d %H:%M:%S")


class OutputHandle(object):
    def __init__(self, outdir):
        self.outdir = outdir

    def __enter__(self):
        if not os.path.exists(self.outdir):
            os.makedirs(self.outdir)
        self.f_intron = open(os.path.join(self.outdir, "intron.bed6"), "w")
        self.f_internal_exon = open(os.path.join(self.outdir, "internal_exon.bed6"), "w")
        self.f_tss_exon = open(os.path.join(self.outdir, "tss_exon.bed6"), "w")
        self.f_tes_exon = open(os.path.join(self.outdir, "tes_exon.bed6"), "w")
        self.f_isoF = open(os.path.join(self.outdir, "isoF.bed12"), "w")
        self.f_isoA = open(os.path.join(self.outdir, "isoA.bed12"), "w")
        self.f_isoR = open(os.path.join(self.outdir, "isoR.bed12"), "w")
        self.f_isoM = open(os.path.join(self.outdir, "isoM.bed12"), "w")
        self.f_isoC = open(os.path.join(self.outdir, "isoC.bed12"), "w")
        self.f_isoP = open(os.path.join(self.outdir, "isoP.bed12"), "w")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def element_handles(self):
        return self.f_intron, self.f_internal_exon, self.f_tss_exon, self.f_tes_exon

    def isoform_handles(self):
        return self.f_isoF, self.f_isoA, self.f_isoR, self.f_isoM, self.f_isoC, self.f_isoP

    def close(self):
        self.f_intron.close()
        self.f_internal_exon.close()
        self.f_tss_exon.close()
        self.f_tes_exon.close()
        self.f_isoF.close()
        self.f_isoA.close()
        self.f_isoR.close()
        self.f_isoM.close()
        self.f_isoC.close()
        self.f_isoP.close()

class TimeOutError(RuntimeError):
    pass

def time_out_handler(signum, frame):
    raise TimeOutError

def check_paraclu(args):
    if args.paraclu_path is not None:
        paraclu_path = args.paraclu_path
        f_paraclu = os.path.join(paraclu_path, "paraclu")
        if not os.path.isfile(f_paraclu):
            raise FileNotFoundError("Can not find file: {0}".format(f_paraclu))
        f_paraclu_cut = os.path.join(paraclu_path, "paraclu-cut.sh")
        if not os.path.isfile(f_paraclu_cut):
            raise FileNotFoundError("Can not find file: {0}".format(f_paraclu_cut))

def main(args):
    """Main entry point allowing external calls
    Args:
      args ([str]): command line parameter list
    """
    args = parse_args(args)
    setup_logging(args.loglevel)
    check_paraclu(args)

    _logger.debug("Starting IGIA ...")

    ngs_obj_list = [SeqFile(x, "NGS") for x in args.ngs_file]
    tgs_obj_list = [SeqFile(x, "TGS") for x in args.tgs_file]
    ext_tss_list = load_txs(args.tss)
    ext_tes_list = load_txs(args.tes)

    out_dir = args.out_dir
    ann = load_ann(args.ann, args.size, out_dir, "ANN")


    # Update Global variables.
    GVAR.RULE = args.rule
    GVAR.TXS_DIFF = args.dtxs
    GVAR.SPLICED_INTRON_PIR_CUTOFF = args.pir
    f_genome = args.f_genome
    paraclu_path = args.paraclu_path

    load_seqinfo(ngs_obj_list)
    _logger.info("Start building linkage ...")
    bam_list = ngs_obj_list + tgs_obj_list
    if ann is not None:
        bam_list += [ann]
    linkage = find_linkage(bam_list)
    _logger.info("Finish building linkage")

    cluster_indx = 0
    with OutputHandle(out_dir) as f_out:
        for chrom, start, end in linkage.iterlinkage():
            try:
                if args.time_out is not None:
                    signal.signal(signal.SIGALRM, time_out_handler)
                    signal.alarm(args.time_out)
                _logger.debug("Start identifying elements in {0}:{1}-{2}".format(chrom, start, end))
                gene_cluster_list = identify_element(
                    chrom, start, end, ngs_obj_list, tgs_obj_list, ext_tss_list, ext_tes_list, ann, f_genome, paraclu_path)
                _logger.debug("Finish identifying elements in {0}:{1}-{2}".format(chrom, start, end))

                for gene_cluster in gene_cluster_list:  # list of gene cluster without any common exon
                    if not gene_cluster.has_element():
                        continue
                    cluster_indx += 1
                    cluster_name = "c_{0}".format(cluster_indx)
                    gene_cluster.write_element2bed6(*f_out.element_handles(), cluster_name)

                    _logger.debug("Start identifying transcript for {0}".format(gene_cluster))
                    trans = identify_transcript(gene_cluster, ann)
                    trans.write2bed12(cluster_name, *f_out.isoform_handles())
                    _logger.debug("Finish identifying transcript for {0}".format(gene_cluster))
                if args.time_out is not None:
                    signal.alarm(0)
            except TimeOutError:
                print("TimeOut: {0}\t{1}\t{2}\n".format(chrom, start, end))
                with open(os.path.join(args.out_dir, "igia_debug_timeout.log"), "a") as f:
                    f.write("TimeOut ({0}s): {1}\t{2}\t{3}\n".format(args.time_out, chrom, start, end))

    _logger.info("End")


def run():
    """Entry point for console_scripts"""
    main(sys.argv[1:])


if __name__ == "__main__":
    run()
