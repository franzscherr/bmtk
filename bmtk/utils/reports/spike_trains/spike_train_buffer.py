import os
import numpy as np
import pandas as pd
import six
import csv

from .core import pop_na, STReader, STBuffer
from .core import SortOrder
from .core import csv_headers

try:
    from mpi4py import MPI
    comm = MPI.COMM_WORLD
    MPI_rank = comm.Get_rank()
    MPI_size = comm.Get_size()
    barrier = comm.Barrier
except:
    MPI_rank = 0
    MPI_size = 1
    barrier = lambda: None


def _spikes_filter1(p, t, time_window, populations):
    return p in populations and time_window[0] <= t <= time_window[1]


def _spikes_filter2(p, t, populations):
    return p in populations


def _spikes_filter3(p, t, time_window):
    return time_window[0] <= t <= time_window[1]


def _create_filter(populations, time_window):
    from functools import partial

    if populations is None and time_window is None:
        return lambda p, t: True
    if populations is None:
        return partial(_spikes_filter3, time_window=time_window)

    populations = [populations] if np.isscalar(populations) else populations
    if time_window is None:
        return partial(_spikes_filter2, populations=populations)
    else:
        return partial(_spikes_filter1, populations=populations, time_window=time_window)


class STMemoryBuffer(STBuffer, STReader):
    def __init__(self, default_population=None, **kwargs):
        self._default_population = default_population or pop_na

        self._node_ids = []
        self._timestamps = []
        self._populations = []
        self._pop_counts = {self._default_population: 0}
        self._units = kwargs.get('units', 'ms')

    def add_spike(self, node_id, timestamp, population=None, **kwargs):
        population = population or self._default_population
        self._node_ids.append(node_id)
        self._timestamps.append(timestamp)
        self._populations.append(population)

        self._pop_counts[population] = self._pop_counts.get(population, 0) + 1

    def add_spikes(self, node_ids, timestamps, population=None, **kwargs):
        if np.isscalar(node_ids):
            node_ids = [node_ids]*len(timestamps)

        for node_id, ts in zip(node_ids, timestamps):
            self.add_spike(node_id, ts, population)

    def import_spikes(self, obj, **kwargs):
        pass

    def flush(self):
        pass

    @property
    def populations(self):
        return list(self._pop_counts.keys())

    def nodes(self, populations=None):
        return list(set(self._node_ids))

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, v):
        self._units = v

    def n_spikes(self, population=None):
        return self._pop_counts.get(population, 0)

    def time_range(self, populations=None):
        return np.min(self._timestamps), np.max(self._timestamps)

    def get_times(self, node_id, population=None, time_window=None, **kwargs):
        population = population or self._default_population
        mask = (np.array(self._node_ids) == node_id) & (np.array(self._populations) == population)
        ts = np.array(self._timestamps)
        if time_window:
            mask &= (time_window[0] <= ts) & (ts <= time_window[1])

        return ts[mask]

    def to_dataframe(self, node_ids=None, populations=None, time_window=None, sort_order=SortOrder.none, **kwargs):
        raise NotImplementedError()

    def spikes(self, node_ids=None, populations=None, time_window=None, sort_order=SortOrder.none, **kwargs):
        if sort_order == SortOrder.by_time:
            sort_indx = np.argsort(self._timestamps)
        elif sort_order == SortOrder.by_time:
            sort_indx = np.argsort(self._node_ids)
        else:
            sort_indx = range(len(self._timestamps))

        filter = _create_filter(populations, time_window)
        for i in sort_indx:
            t = self._timestamps[i]
            p = self._populations[i]
            if filter(p=p, t=t):
                yield t, p, self._node_ids[i]

        raise StopIteration

    def __len__(self):
        return len(self.to_dataframe())


class STCSVBuffer(STBuffer, STReader):
    def __init__(self, cache_dir=None, default_population=None, **kwargs):
        self._default_population = default_population or pop_na

        self._cache_dir = cache_dir or '.'
        self._buffer_filename = self._cache_fname(self._cache_dir)
        self._buffer_handle = open(self._buffer_filename, 'w')

        self._pop_counts = {self._default_population: 0}
        self._nspikes = 0
        self._units = kwargs.get('units', 'ms')

    def _cache_fname(self, cache_dir):
        if not os.path.exists(self._cache_dir):
            os.mkdirs(self._cache_dir)
        return os.path.join(cache_dir, '.bmtk.spikes.cache.csv')

    def add_spike(self, node_id, timestamp, population=None, **kwargs):
        population = population or pop_na

        self._buffer_handle.write('{} {} {}\n'.format(timestamp, population, node_id))
        self._nspikes += 1
        self._pop_counts[population] = self._pop_counts.get(population, 0) + 1

    def add_spikes(self, node_ids, timestamps, population=None, **kwargs):
        if np.isscalar(node_ids):
            for ts in timestamps:
                self.add_spike(node_ids, ts, population)
        else:
            for node_id, ts in zip(node_ids, timestamps):
                self.add_spike(node_id, ts, population)

    def import_spikes(self, obj):
        pass

    @property
    def populations(self):
        return list(self._pop_counts.keys())

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, v):
        self._units = v

    def nodes(self, populations=None):
        return list(set(self._node_ids))

    def n_spikes(self, population=None):
        return self._pop_counts.get(population, 0)

    def time_range(self, populations=None):
        return np.min(self._timestamps), np.max(self._timestamps)

    def get_times(self, node_id, population=None, time_window=None, **kwargs):
        return np.array([t[0] for t in self.spikes(population=population, time_window=time_window) if t[1] == node_id])

    def to_dataframe(self, node_ids=None, populations=None, time_window=None, sort_order=SortOrder.none, **kwargs):
        raise NotImplementedError()

    def flush(self):
        self._buffer_handle.flush()

    def close(self):
        self._buffer_handle.close()
        if os.path.exists(self._buffer_filename):
            os.remove(self._buffer_filename)

    def spikes(self, node_ids=None, populations=None, time_window=None, sort_order=SortOrder.none, **kwargs):
        self.flush()
        self._sort_buffer_file(self._buffer_filename, sort_order)
        filter = _create_filter(populations, time_window)
        with open(self._buffer_filename, 'r') as csvfile:
            csv_reader = csv.reader(csvfile, delimiter=' ')
            for row in csv_reader:
                t = float(row[0])
                p = row[1]
                if filter(p=p, t=t):
                    yield t, p, int(row[2])

        raise StopIteration

    def _sort_buffer_file(self, file_name, sort_order):
        if sort_order == SortOrder.by_time:
            sort_col = 'time'
        elif sort_order == SortOrder.by_id:
            sort_col = 'node'
        else:
            return

        tmp_spikes_ds = pd.read_csv(file_name, sep=' ', names=['time', 'population', 'node'])
        tmp_spikes_ds = tmp_spikes_ds.sort_values(by=sort_col)
        tmp_spikes_ds.to_csv(file_name, sep=' ', index=False, header=False)


class STMPIBuffer(STCSVBuffer):
    def __init__(self, cache_dir=None, default_population=None, **kwargs):
        self.mpi_rank = kwargs.get('MPI_rank', MPI_rank)
        self.mpi_size = kwargs.get('MPI_size', MPI_size)
        super(STMPIBuffer, self).__init__(cache_dir, default_population=default_population, **kwargs)

    def _cache_fname(self, cache_dir):
        if self.mpi_rank == 0:
            if not os.path.exists(self._cache_dir):
                os.mkdirs(self._cache_dir)
        barrier()

        return os.path.join(self._cache_dir, '.bmtk.spikes.cache.node{}.csv'.format(self.mpi_rank))

    def _all_cached_files(self):
        return [os.path.join(self._cache_dir, '.bmtk.spikes.cache.node{}.csv'.format(r)) for r in range(MPI_size)]

    @property
    def populations(self):
        self._gather()
        return list(self._pop_counts.keys())

    def n_spikes(self, population=None):
        self._gather()
        return self._pop_counts.get(population, 0)

    def _gather(self):
        self._pop_counts = {}
        for fn in self._all_cached_files():
            with open(fn, 'r') as csvfile:
                csv_reader = csv.reader(csvfile, delimiter=' ')
                for row in csv_reader:
                    pop = row[1]
                    self._pop_counts[pop] = self._pop_counts.get(pop, 0) + 1

    def spikes(self, node_ids=None, populations=None, time_window=None, sort_order=SortOrder.none, **kwargs):
        self.flush()

        filter = _create_filter(populations, time_window)
        if sort_order == SortOrder.by_time or sort_order == SortOrder.by_id:
            for file_name in self._all_cached_files():
                self._sort_buffer_file(file_name, sort_order)

            return self._sorted_itr(filter, 0 if sort_order == SortOrder.by_time else 1)
        else:
            return self._unsorted_itr(filter)

    def _unsorted_itr(self, filter):
        for fn in self._all_cached_files():
            with open(fn, 'r') as csvfile:
                csv_reader = csv.reader(csvfile, delimiter=' ')
                for row in csv_reader:
                    t = float(row[0])
                    p = row[1]
                    if filter(p=p, t=t):
                        yield t, p, int(row[2])

        raise StopIteration

    def _sorted_itr(self, filter, sort_col):
        import heapq

        def next_row(csv_reader):
            try:
                rn = next(csv_reader)
                row = [float(rn[0]), rn[1], int(rn[2])]
                return row[sort_col], row, csv_reader
            except StopIteration:
                return None

        h = []
        readers = [next_row(csv.reader(open(fn, 'r'), delimiter=' ')) for fn in self._all_cached_files()]
        for r in readers:
            heapq.heappush(h, r)

        while h:
            v, row, csv_reader = heapq.heappop(h)
            n = next_row(csv_reader)
            if n:
                heapq.heappush(h, n)

            if filter(row[1], row[2]):
                yield row
