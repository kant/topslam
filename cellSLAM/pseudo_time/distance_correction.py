#===============================================================================
# Copyright (c) 2016, Max Zwiessele
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of cellSLAM nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#===============================================================================cellSLAM.pseudo_timefrom . import distances

from scipy.sparse.csgraph import minimum_spanning_tree, dijkstra
from scipy.sparse import csr_matrix, find, lil_matrix
from scipy.cluster.hierarchy import average, fcluster, dendrogram
from scipy.spatial.distance import pdist, squareform
from .distances import mean_embedding_dist
from ..landscape import waddington_landscape
from ..plotting import plot_graph_nodes

import matplotlib.pyplot as plt

import numpy as np

class ManifoldCorrection(object):
    def __init__(self, gplvm, distance=mean_embedding_dist, dimensions=None):
        """
        Construct a correction class for the BayesianGPLVM given.

        All evaluations on this object are lazy, so do not change attributes
        at runtime in order to have a consistent model.

        :param [GPy.models.BayesianGPLVM,GPy.models.GPLVM] gplvm:
            an optimized GPLVM or BayesianGPLVM model from GPy
        :param func dist: dist(X,G), the distance to use for pairwise distances
            in X using the cellSLAM embedding G
        :param array-like dimensions: The dimensions of the latent space to use [default: self.gplvm.get_most_significant_input_dimensions()[:2]]
        """
        self.gplvm = gplvm
        self.distance = distance
        if dimensions is None:
            dimensions = self.gplvm.get_most_significant_input_dimensions()[:2]
        self.dimensions = dimensions

    @property
    def X(self):
        return self.Xgplvm[:,self.dimensions]

    @property
    def Xgplvm(self):
        if getattr(self, '_X', None) is None:
            try:
                _X = self.gplvm.X.mean
                _X.mean
            except AttributeError:
                # not bayesian GPLVM
                _X = self.gplvm.X
            # Make sure we only take the dimensions we want to use:
            self._X = np.zeros(_X.shape)
            msi = self.dimensions
            self._X[:, msi] = _X[:,msi]
        return self._X

    @property
    def G(self):
        if getattr(self, '_G', None) is None:
            self._G = self.gplvm.predict_wishard_embedding(self.Xgplvm)
        return self._G

    @property
    def manifold_corrected_distance_matrix(self):
        """
        Returns the distances between all pairs of inputs, corrected for
        the cellSLAM embedding.
        """
        if getattr(self, '_M', None) is None:
            self._M = csr_matrix(self.distance(self.Xgplvm, self.G))
        return self._M

    @property
    def minimal_spanning_tree(self):
        """
        Create a minimal spanning tree using the distance correction method given.

        You can explore different distance corrections in cellSLAM.pseudo_time.distances.
        """
        if getattr(self, '_mst', None) is None:
            self._mst = minimum_spanning_tree(self.manifold_corrected_distance_matrix)
        return self._mst

    @property
    def graph(self):
        """
        Return the correction graph to use for this cellSLAM correction object.
        """
        raise NotImplemented("Implement the graph extraction property for this class")

    def _prep_distances(self):
        self._graph_distances, self._predecessors = dijkstra(self.graph, directed=False, return_predecessors=True)

    @property
    def graph_distances(self):
        """
        Return all distances along the graph.

        :param knn_graph: The sparse matrix encoding the knn-graph to compute distances on.
        :param bool return_predecessors: Whether to return the predecessors of each node in the graph, this is for reconstruction of paths.
        """
        if getattr(self, '_graph_distances', None) is None:
            self._prep_distances()
        return self._graph_distances

    @property
    def graph_predecessors(self):
        """
        Return the predecessors of each node for this graph correction.

        This is used for path reconstruction along this graphs shortest paths.
        """
        if getattr(self, '_predecessors', None) is None:
            self._prep_distances()
        return self._predecessors

    @property
    def linkage_along_graph(self):
        """
        Return the UPGMA linkage matrix for the distances along the graph.
        """
        if getattr(self, '_dist_linkage', None) is None:
            self._dist_linkage = average(squareform(self.graph_distances))
        return self._dist_linkage

    @property
    def distances_in_structure(self):
        """
        Return the structure distances, where each edge along the graph has a
        distance of one, such that the distance just means the number of
        hops to make in order to get from one point to another.

        This can be very helpful in doing structure analysis
        and clustering of the cellSLAM embedded data points.

        returns hops, the pairwise number of hops between points along the tree.
        """
        if getattr(self, '_struct_distances', None) is None:
            self._struct_distances = dijkstra(self.graph, directed=False, unweighted=True, return_predecessors=False)
        return self._struct_distances

    @property
    def linkage_in_structure(self):
        """
        Return the UPGMA linkage matrix based on the correlation structure of
        the cellSLAM embedding MST
        """
        if getattr(self, '_struct_linkage', None) is None:
            self._struct_linkage = average(pdist(self.distances_in_structure, metric='correlation'))
        return self._struct_linkage

    def cluster(self, linkage, num_classes):
        """
        Cluster the linkage matrix into num_classes number of classes.
        """
        return fcluster(linkage, t=num_classes, criterion='maxclust')

    def plot_dendrogram(self, linkage, **kwargs):
        """
        plot a dendrogram for the linkage matrix with leaf labels. The kwargs go
        directly into the scipy function :py:func:`scipy.cluster.hierarchy.dendrogram`
        """
        return dendrogram(linkage, **kwargs)

    def get_time_graph(self, start):
        """
        Returns a graph, where all edges are filled with the distance from
        `start`. This is mostly for plotting purposes, visualizing the
        time along the tree, starting from `start`.
        """
        test_graph = csr_matrix(self.graph.shape)
        pt = self.get_pseudo_time(start)
        for i,j in zip(*find(self.graph)[:2]):
            test_graph[i,j] = pt[j]
            if j == start:
                test_graph[i,j] = pt[i]
        return test_graph

    def get_longest_path(self, start, report_all=False):
        """
        Get the longest path from start ongoing. This usually coincides with the
        backbone of the tree, starting from the starting point. If the latent
        structure divides into substructures, this is either of the two (if
        two paths have the same lengths). If report_all is True, we find all backbones
        with the same number of edges.
        """
        S = self.distances_in_structure
        preds = self.graph_predecessors
        distances = S[start]
        maxdist = S[start].max()
        ends = (S[start]==maxdist).nonzero()[0]
        paths = []
        for end in ends:
            pre = end
            path = []
            while pre != start:
                path.append(pre)
                pre = preds[start,pre]
            path.append(start)
            if not report_all:
                return path[::-1]
            else:
                paths.append(path[::-1])
        return paths
    
    def get_pseudo_time(self, start, estimate_direction=True):
        """
        Returns the pseudo times along the tree correction of the cellSLAM
        for the given starting point `start` to all other points (including `start`).

        If the starting point is not a leaf, we will select a direction try
        to estimate a direction for the tree, based on longest paths.

        :param int start: The index of the starting point in self.X
        :param bool estimate_direction: Whether to estimate a direction or not
        """
        pseudo_time = self.graph_distances[start].copy()
        if estimate_direction:
            S = self.distances_in_structure.copy()
            preds = self.graph_predecessors.copy()
            maxdist = S[start].argmax()
            before = preds[maxdist, start]    
            
            pseudo_time[preds[:,start]!=before] *= -1
            if np.sum(pseudo_time<0) > np.sum(pseudo_time>=0):
                pseudo_time *= -1
        pseudo_time -= pseudo_time.min()
        pseudo_time /= pseudo_time.max()
        return pseudo_time
    
    def plot_waddington_landscape_3d(self, labels=None, ulabels=None, resolution=60, ncol=5, cmap='terrain', cstride=1, rstride=1, xmargin=(.075, .075), ymargin=(.075, .075), **kw):
        """
        Plot a waddngton landscape with data in 3D.
        Xgrid and wad are the landscape (surface plot, [resolution x resolution])
        and X and wadX are the datapoints as returned by
        Xgrid, wadXgrid, X, wadX = landscape(m).
    
        ulabels and labels are the unique labels and labels for each datapoint of X.
        ncol defines the number of columns in the legend above the plot.
    
        Returns the 3d axis instance of mplot3d.
        """    
        if labels is None:
            labels = np.zeros(self.gplvm.X.shape[0])
    
        if ulabels is None:
            ulabels = []
            for l in labels:
                if l not in ulabels:
                    ulabels.append(l)
            ulabels = np.asarray(ulabels)
        
        from mpl_toolkits.mplot3d import Axes3D
        fig = plt.figure(figsize=(4.66666666655,3.5), tight_layout=True)
        ax = fig.add_subplot(111, projection='3d')
    
        from GPy.plotting import Tango
        Tango.reset()
    
        from itertools import cycle
        colors = cycle(Tango.mediumList)
        markers = cycle('<>^vsd')
    
        r = lambda x: x.reshape(resolution, resolution).T
        
        (Xgrid, wadXgrid, X, wadX) = waddington_landscape(self.gplvm, resolution, xmargin, ymargin)
    
        ax.plot_surface(r(Xgrid[:,0]), r(Xgrid[:,1]), r(wadXgrid), cmap=cmap, rstride=rstride, cstride=cstride, linewidth=0, **kw)
    
        for lab in ulabels:
            fil = labels==lab
            c = [c_/255. for c_ in Tango.hex2rgb(next(colors))]
            ax.scatter(X[fil, :][:, 0], X[fil, :][:, 1], wadX[fil],
                       edgecolor='k', linewidth=.4,
                       c=c, label=lab, marker=next(markers))
    
        ax.set_zlim(-1.5,1.5)
        mi, ma = Xgrid.min(0), Xgrid.max(0)
        ax.set_xlim(mi[0], ma[0])
        ax.set_ylim(mi[1], ma[1])
    
        ax.legend(ncol=ncol, loc=0)
    
        return ax
    
    def plot_waddington_landscape(self, ax=None, resolution=60, xmargin=(.075, .075), ymargin=(.075, .075), cmap='Greys'):
        if ax is None:
            fig, ax = plt.subplots()
        else:
            fig = ax.figure
    
        (Xgrid, wadXgrid, X, wadX) = waddington_landscape(self.gplvm, resolution, xmargin, ymargin)
        r = lambda x: x.reshape(resolution, resolution).T
        CS = ax.contourf(r(Xgrid[:,0]), r(Xgrid[:,1]), r(wadXgrid), linewidths=.6, cmap=cmap)
        mi, ma = Xgrid.min(0), Xgrid.max(0)
        ax.set_xlim(mi[0], ma[0])
        ax.set_ylim(mi[1], ma[1])
    
        return ax
    
    def plot_time_graph(self, labels=None, ulabels=None, start=0, startoffset=(10,5), ax=None, cmap='magma'):
        
        if ulabels is None and labels is not None:
            ulabels = []
            for l in labels:
                if l not in ulabels:
                    ulabels.append(l)
            ulabels = np.asarray(ulabels)
        
        if ax is None:
            fig, ax = plt.subplots()
        else:
            fig = ax.figure
            
        self.plot_graph_nodes(labels, ulabels, start, ax, cmap=cmap)
        if labels is not None:
            self.plot_graph_labels(labels, ulabels=ulabels, start=start, ax=ax, cmap=cmap)
        self.plot_time_graph_edges(start, startoffset, ax, cmap)    
        #ax.legend(bbox_to_anchor=(0., 1.02, 1.2, .102), loc=3,
        #           ncol=4, mode="expand", borderaxespad=0.)
        return ax

    def plot_time_graph_edges(self, start=0, startoffset=(10,5), ax=None, cmap='magma'):
        if ax is None:
            fig, ax = plt.subplots()
        else:
            fig = ax.figure
        import networkx as nx
        X = self.X
        G = nx.Graph(self.get_time_graph(start))
        ecols = [e[2]['weight'] for e in G.edges(data=True)]
        cmap = plt.get_cmap(cmap)    
        pos = dict([(i, x) for i, x in zip(range(X.shape[0]), X)])
        edges = nx.draw_networkx_edges(G, pos=pos, ax=ax, edge_color=ecols, edge_cmap=cmap, width=1)
    
        cbar = fig.colorbar(edges, ax=ax, pad=.01, fraction=.1, ticks=[], drawedges=False)
        cbar.ax.set_frame_on(False)
        cbar.solids.set_edgecolor("face")
        cbar.set_label('pseudo time')
    
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_frame_on(False)
    
        #ax.scatter(*X[start].T, edgecolor='red', lw=1.5, facecolor='none', s=50, label='start')
        ax.annotate('start', xy=X[start].T, xycoords='data',
                        xytext=startoffset, textcoords='offset points',
                        size=9,
                        color='.4',
                        bbox=dict(boxstyle="round", fc="0.8", ec='1', pad=.01),
                        arrowprops=dict(arrowstyle="fancy",
                                        fc="0.6", ec="none",
                                        #patchB=el,
                                        connectionstyle="angle3,angleA=17,angleB=-90"),
                        )


    def plot_graph_nodes(self, labels=None, ulabels=None, start=0, ax=None, cmap='magma', cmap_index=None, box=True, text_kwargs=None, **scatter_kwargs):
        #Tango = GPy.plotting.Tango
        #Tango.reset()
        import itertools
        marker = itertools.cycle('<>sd^')
    
        if labels is None:
            labels = np.zeros(self.X.shape[0])
    
        if ulabels is None:
            ulabels = []
            for l in labels:
                if l not in ulabels:
                    ulabels.append(l)
            ulabels = np.asarray(ulabels)
        
        if ax is None:
            fig, ax = plt.subplots()
        else:
            fig = ax.figure
    
        X = self.X
        pt = self.get_pseudo_time(start)
    
        if len(ulabels) <= 1:
            ax.scatter(*X.T, linewidth=.1, c=pt, alpha=.8, edgecolor='w', marker=next(marker), label=None, cmap=cmap)
        else:
            _, col, mi, ma = _get_label_pos(X, pt, labels, ulabels)
            colors = _get_colors(cmap, col, mi, ma, cmap_index)
            for l in ulabels:
                #c = Tango.nextMedium()
                c, r = colors[l]
                fil = (labels==l)
                ax.scatter(*X[fil].T, linewidth=.1, facecolor=c, alpha=.8, edgecolor='w', marker=next(marker), label=l)
        
    def plot_graph_labels(self, labels, ulabels=None, start=0, ax=None, cmap='magma', cmap_index=None, box=True, text_kwargs=None, **scatter_kwargs):
        #Tango = GPy.plotting.Tango
        #Tango.reset()
        import itertools
        marker = itertools.cycle('<>sd^')
    
        if ulabels is None:
            ulabels = []
            for l in labels:
                if l not in ulabels:
                    ulabels.append(l)
            ulabels = np.asarray(ulabels)
        
        if ax is None:
            fig, ax = plt.subplots()
        else:
            fig = ax.figure
    
        X = self.X
        pt = self.get_pseudo_time(start)
    
        label_pos, col, mi, ma = _get_label_pos(X, pt, labels, ulabels)
        colors = _get_colors(cmap, col, mi, ma, cmap_index)
        for l in ulabels:
            #c = Tango.nextMedium()
            c, r = colors[l]
            p = label_pos[l]
            rgbc = c#[_c/255. for _c in Tango.hex2rgb(c)]
            if r <.5:
                ec = 'w'
            else:
                ec = 'k'
            if box:
                fc = list(rgbc)
                #fc[-1] = .7
                props = dict(boxstyle='round', facecolor=fc, alpha=0.8, edgecolor=ec)
            else:
                props = dict()
            ax.text(p[0], p[1], l, alpha=.9, ha='center', va='center', color=ec, bbox=props, **text_kwargs or {})


def _get_colors(cmap, col, mi, ma, cmap_index):
    if cmap_index is None:
        cmap = plt.cm.get_cmap(cmap)
        colors = dict([(l, (cmap((col[l]-mi)/(ma-mi)), (col[l]-mi)/(ma-mi))) for l in col])
    else:
        cmap = sns.color_palette(cmap, len(col))[cmap_index]
        r = np.linspace(0,1,len(col))[cmap_index]
        colors = dict([(l, (cmap, r)) for l in col])
    return colors

def _get_sort_dict(labels, ulabels):
    sort_dict = {}#np.empty(labels.shape, dtype=int)
    curr_i = 0
    for i, l in enumerate(ulabels):
        hits = labels==l
        sort_dict[l] = np.where(hits)[0]
        curr_i += hits.sum()
    return sort_dict

def _get_label_pos(X, pt, labels, ulabels):
    sort_dict = _get_sort_dict(labels, ulabels)
    label_pos = {}
    col = {}
    mi, ma = np.inf, 0
    for l in ulabels:
        label_pos[l] = X[sort_dict[l]].mean(0)
        c = pt[sort_dict[l]].mean()
        col[l] = c
        mi = min(mi, c)
        ma = max(ma, c)
    return label_pos, col, mi, ma
