# Author: F. Alex Wolf (http://falexwolf.de)
"""Differential Gene Expression Analysis

This is a Beta Version of a tool for differential gene expression testing
between sets detected in previous tools. Tools such as dpt, cluster,...
"""

import numpy as np
import pandas as pd
from math import sqrt, floor
from scipy.sparse import issparse
from scipy.stats import rankdata
from scipy.stats import norm
from .. import utils
from .. import logging as logg
from ..preprocessing import simple

def rank_genes_groups(
        adata,
        groupby,
        groups='all',
        group_reference=None,
        n_genes=100,
        compute_distribution=False,
        only_positive=True,
        copy=False,
        test_type='t_test'):
    """Rank genes according to differential expression [Wolf17]_.

    Rank genes by differential expression. By default, a t-test-like ranking is
    used, in which means are normalized with variances. Soon, a Wilcoxon-rank
    test and other alternatives will be provided.

    Parameters
    ----------
    adata : `AnnData`
        Annotated data matrix.
    groupby : `str`
        The key of the sample grouping to consider.
    groups : `str`, `list`, optional (default: `'all'`)
        Subset of groups, e.g. `['g1', 'g2', 'g3']`, to which comparison shall
        be restricted. If not passed, a ranking will be generated for all
        groups.
    group_reference : `str` or `None`, optional (default: `None`)
        If `None`, compare each group to the union of the rest of the group.  If
        a group identifier, the comparison will be with respect to this group.
    n_genes : `int` (default: 100)
        How many genes to rank by default.
    compute_distribution : `bool`
        If `True`, also computes the distribution for top-ranked genes, which
        can be visualized using `sc.pl.rank_genes_groups_violin(adata)`.
    test_type : 't_test' or 'wilcoxon' (default: 't_test')
        If 't_test', use t_test to calculate test statistics. If 'wilcoxon', use Wilcoxon-Rank-Sum
        to calculate test statistic.
    Returns
    -------
    rank_genes_groups_gene_zscores : np.ndarray of dtype float (adata.add)
        Array of shape (number of comparisons) × (number of genes) storing the
        zscore of the each gene for each test.
    rank_genes_groups_gene_names : np.ndarray of dtype str (adata.add)
        Array of shape (number of comparisons). Stores the labels for each comparison,
        for example "C1 vs. C2" when comparing category 'C1' with 'C2'.
    """
    logg.info('find differentially expressed genes', r=True)
    adata = adata.copy() if copy else adata
    n_genes_user = n_genes
    utils.check_adata(adata)
    # for clarity, rename variable
    groups_order = groups
    if isinstance(groups_order, list) and isinstance(groups_order[0], int):
        groups_order = [str(n) for n in groups_order]
    if group_reference is not None and group_reference not in set(groups_order):
        groups_order += [group_reference]
    if (group_reference is not None
        and group_reference not in set(adata.add[groupby + '_order'])):
        raise ValueError('group_reference = {} needs to be one of groupby = {}.'
                         .format(group_reference, groupby))
    groups_order, groups_masks = utils.select_groups(
        adata, groups_order, groupby)
    adata.add['rank_genes_groups'] = groupby
    adata.add['rank_genes_groups_order'] = groups_order
    X = adata.X

    rankings_gene_zscores = []
    rankings_gene_names = []
    n_groups = groups_masks.shape[0]
    n_genes = X.shape[1]
    ns = np.zeros(n_groups, dtype=int)
    for imask, mask in enumerate(groups_masks):
        ns[imask] = np.where(mask)[0].size
    # TODO: Add logging such that test-type is included
    logg.info('... consider "{}":'.format(groupby), groups_order,
              'with sample numbers', ns)
    if group_reference is not None:
        ireference = np.where(groups_order == group_reference)[0][0]
    reference_indices = np.arange(adata.n_vars, dtype=int)

    # Here begins the part that is test-specific.

    if test_type not in {'t_test', 'wilcoxon'}:
        # TODO: Print Error Message in logging
        logg.warn('Test_type should be either "wilcoxon" or "t_test". T-test is being used as default' )
        # For convenience, and to avoid total collapse, set test_type to t_test
        test_type='t_test'

    if test_type is 't_test':
        # loop over all masks and compute means, variances and sample numbers
        # Definition of n_groups and n_genes was moved ahead since required for all test-types

        means = np.zeros((n_groups, n_genes))
        vars = np.zeros((n_groups, n_genes))
        # Definition of ns Moved ahead
        for imask, mask in enumerate(groups_masks):
            means[imask], vars[imask] = simple._get_mean_var(X[mask])
            # Definition of ns moved ahead
        # The following code parts were moved ahead since required by all test-types: Logging, ireference,
        # ankings_gene_zscores = [] and rankings_gene_names = [] and reference_indices


        # test each either against the union of all other groups
        # or against a specific group


        for igroup in range(n_groups):
            if group_reference is None:
                mask_rest = ~groups_masks[igroup]
            else:
                if igroup == ireference: continue
                else: mask_rest = groups_masks[ireference]
            mean_rest, var_rest = simple._get_mean_var(X[mask_rest])
            # Make a more conservative assumption on the variance reduction
            # in the reference. Instead of this
            ns_rest = np.where(mask_rest)[0].size
            # use this
            # ns_rest = ns[igroup]
            denominator = np.sqrt(vars[igroup]/ns[igroup] + var_rest/ns_rest)
            denominator[np.flatnonzero(denominator == 0)] = np.nan
            zscores = (means[igroup] - mean_rest) / denominator
            zscores[np.isnan(zscores)] = 0
            zscores = zscores if only_positive else np.abs(zscores)
            partition = np.argpartition(zscores, -n_genes_user)[-n_genes_user:]
            partial_indices = np.argsort(zscores[partition])[::-1]
            global_indices = reference_indices[partition][partial_indices]
            rankings_gene_zscores.append(zscores[global_indices])
            rankings_gene_names.append(adata.var_names[global_indices])
            if compute_distribution:
                mask = groups_masks[igroup]
                for gene_counter in range(n_genes_user):
                    gene_idx = global_indices[gene_counter]
                    X_col = X[mask, gene_idx]
                    if issparse(X): X_col = X_col.toarray()[:, 0]
                    identifier = _build_identifier(groupby, groups_order[igroup],
                                                   gene_counter, adata.var_names[gene_idx])
                    full_col = np.empty(adata.n_smps)
                    full_col[:] = np.nan
                    full_col[mask] = (X_col - mean_rest[gene_idx]) / denominator[gene_idx]
                    adata.smp[identifier] = full_col
    elif test_type is 'wilcoxon':
        # Wilcoxon-rank-sum test is usually more powerful in detecting marker genes
        # Limit maximal RAM that is required by the calculation. Currently set fixed to roughly 100 MByte
        CONST_MAX_SIZE = 10000000
        ns_rest = np.zeros(n_groups, dtype=int)
        # initialize space for z-scores
        zscores = np.zeros(n_genes)
        # First loop: Loop over all genes
        if group_reference is not None:
            for imask, mask in enumerate(groups_masks):
                if imask == ireference:
                    continue
                else:
                    mask_rest = groups_masks[ireference]
                ns_rest[imask] = np.where(mask_rest)[0].size
                if ns_rest[imask] <= 25 or ns[imask] <= 25:
                    logg.hint("Few observations in a group for normal approximation (<=25). Lower test accuracy.")
                n_active = ns[imask]
                m_active = ns_rest[imask]
                # Now calculate gene expression ranking in batches:
                batch = []
                # Calculate batch frames
                n_genes_max_batch = floor(CONST_MAX_SIZE / (n_active + m_active))
                if n_genes_max_batch < n_genes - 1:
                    batch_index = n_genes_max_batch
                    while batch_index < n_genes - 1:
                        batch.append(batch_index)
                        batch_index = batch_index + n_genes_max_batch
                    batch.append(n_genes - 1)
                else:
                    batch.append(n_genes - 1)
                left = 0
                # Calculate rank sums for each batch for the current mask
                for batch_index, right in enumerate(batch):
                    # Check if issparse is true: AnnData objects are currently sparse.csr or ndarray.
                    if issparse(X):
                        df1 = pd.DataFrame(data=X[mask, left:right].todense())
                        df2 = pd.DataFrame(data=X[mask_rest, left:right].todense(),
                                           index=np.arange(start=n_active, stop=n_active + m_active))
                    else:
                        df1 = pd.DataFrame(data=X[mask, left:right])
                        df2 = pd.DataFrame(data=X[mask_rest, left:right],
                                           index=np.arange(start=n_active, stop=n_active + m_active))
                    df1 = df1.append(df2)
                    ranks = df1.rank()
                    # sum up adjusted_ranks to calculate W_m,n
                    zscores[left:right] = np.sum(ranks.loc[0:n_active, :])
                    left = right + 1
                zscores[np.isnan(zscores)] = 0
                partition = np.argpartition(zscores, -n_genes_user)[-n_genes_user:]
                partial_indices = np.argsort(zscores[partition])[::-1]
                global_indices = reference_indices[partition][partial_indices]
                rankings_gene_zscores.append(zscores[global_indices])
                rankings_gene_names.append(adata.var_names[global_indices])
                if compute_distribution:
                    # remove line: current mask already available
                    # Add calculation of means, var: (Unnecessary for wilcoxon if compute distribution=False)
                    mean, vars = simple._get_mean_var(X[mask])
                    mean_rest, var_rest = simple._get_mean_var(X[mask_rest])
                    denominator = np.sqrt(vars / ns[imask] + var_rest / ns_rest[imask])
                    denominator[np.flatnonzero(denominator == 0)] = np.nan
                    for gene_counter in range(n_genes_user):
                        gene_idx = global_indices[gene_counter]
                        X_col = X[mask, gene_idx]
                        if issparse(X): X_col = X_col.toarray()[:, 0]
                        identifier = _build_identifier(groupby, groups_order[imask],
                                                       gene_counter, adata.var_names[gene_idx])
                        full_col = np.empty(adata.n_smps)
                        full_col[:] = np.nan
                        full_col[mask] = (X_col - mean_rest[gene_idx]) / denominator[gene_idx]
                        adata.smp[identifier] = full_col

        # If no reference group exists, ranking needs only to be done once (full mask)
        else:
            zscores=np.zeros((n_groups,n_genes))
            batch = []
            n_cells=X.shape[0]
            n_genes_max_batch = floor(CONST_MAX_SIZE / n_cells)
            if n_genes_max_batch < n_genes - 1:
                batch_index = n_genes_max_batch
                while batch_index < n_genes - 1:
                    batch.append(batch_index)
                    batch_index = batch_index + n_genes_max_batch
                batch.append(n_genes - 1)
            else:
                batch.append(n_genes - 1)
            left = 0
            for batch_index, right in enumerate(batch):
                # Check if issparse is true
                if issparse(X):
                    df1 = pd.DataFrame(data=X[:, left:right].todense())
                else:
                    df1 = pd.DataFrame(data=X[:, left:right])
                ranks = df1.rank()
                # sum up adjusted_ranks to calculate W_m,n
                for imask, mask in enumerate(groups_masks):
                    zscores[imask,left:right] = np.sum(ranks.loc[mask, :])
                left = right + 1

            for imask, mask in enumerate(groups_masks):

                zscores[imask,:] = (zscores[imask,:] - (ns[imask] * (n_cells + 1) / 2)) / sqrt(
                    (ns[imask] * (n_cells-ns[imask]) * (n_cells + 1) / 12))
                zscores = zscores if only_positive else np.abs(zscores)
                zscores[np.isnan(zscores)] = 0
                partition = np.argpartition(zscores[imask,:], -n_genes_user)[-n_genes_user:]
                partial_indices = np.argsort(zscores[imask,partition])[::-1]
                global_indices = reference_indices[partition][partial_indices]
                rankings_gene_zscores.append(zscores[imask, global_indices])
                rankings_gene_names.append(adata.var_names[global_indices])
                if compute_distribution:
                    mean, vars = simple._get_mean_var(X[mask])
                    mean_rest, var_rest = simple._get_mean_var(X[~mask])
                    denominator = np.sqrt(vars / ns[imask] + var_rest / (n_cells-ns[imask]))
                    denominator[np.flatnonzero(denominator == 0)] = np.nan
                    for gene_counter in range(n_genes_user):
                        gene_idx = global_indices[gene_counter]
                        X_col = X[mask, gene_idx]
                        if issparse(X): X_col = X_col.toarray()[:, 0]
                        identifier = _build_identifier(groupby, groups_order[imask],
                                                       gene_counter, adata.var_names[gene_idx])
                        full_col = np.empty(adata.n_smps)
                        full_col[:] = np.nan
                        full_col[mask] = (X_col - mean_rest[gene_idx]) / denominator[gene_idx]
                        adata.smp[identifier] = full_col



    # Here ends the test-specific part, do logging



    groups_order_save = groups_order
    if group_reference is not None:
        groups_order_save = [g for g in groups_order if g != group_reference]
    adata.add['rank_genes_groups_gene_scores'] = np.rec.fromarrays(
        [n for n in rankings_gene_zscores],
        dtype=[(rn, 'float32') for rn in groups_order_save])
    adata.add['rank_genes_groups_gene_names'] = np.rec.fromarrays(
        [n for n in rankings_gene_names],
        dtype=[(rn, 'U50') for rn in groups_order_save])
    logg.m('    finished', t=True, end=' ')
    logg.m('and added\n'
           '    "rank_genes_groups_gene_names", np.recarray to be indexed by the `groups` (adata.add)\n'
           '    "rank_genes_groups_gene_zscores", the scores (adata.add)\n'
           '    "rank_genes_...", distributions of top-ranked genes (adata.smp)')
    return adata if copy else None


def _build_identifier(groupby, name, gene_counter, gene_name):
    return 'rank_genes_{}_{}_{}_{}'.format(
        groupby, name, gene_counter, gene_name)
