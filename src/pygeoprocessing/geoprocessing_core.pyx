import os
import tempfile
import logging
import time
import sys
import traceback

cimport numpy
import numpy
cimport cython
from libcpp.map cimport map

from libc.math cimport sqrt
from libc.math cimport exp
from libc.math cimport ceil

from osgeo import gdal
import pygeoprocessing

DEFAULT_GTIFF_CREATION_OPTIONS = (
    'TILED=YES', 'BIGTIFF=YES', 'COMPRESS=LZW',
    'BLOCKXSIZE=256', 'BLOCKYSIZE=256')
LOGGER = logging.getLogger('pygeoprocessing.geoprocessing_core')

cdef float NODATA = -1.0


class MaskWrapper(object):
    def __init__(self, base_nodata):
        self.base_nodata = base_nodata

    def __call__(self, base_array):
        result = numpy.empty(base_array.shape, dtype=numpy.int8)
        result[:] = NODATA
        valid_mask = base_array != self.base_nodata
        result[valid_mask] = base_array[valid_mask] != 0
        return result

@cython.boundscheck(False)
@cython.wraparound(False)
@cython.cdivision(True)
def distance_transform_edt(base_mask_raster_path_band, target_distance_path):
    """Calculate the Euclidean distance transform.

    Parameters:
        base_mask_raster_path_band (tuple): a (path, band index) tuple to
            calculate value from non-zero valued pixels.

        target_distance_path (string): a path to a raster created by this
        function with same dimensions and projection as base_mask_path where
        all non-zero values of base_mask_path are equal to the euclidean
        distance to the closest 0 pixel.

    Returns:
        None."""
    cdef int yoff, row_index, block_ysize, win_ysize, n_rows
    cdef int xoff, block_xsize, win_xsize, n_cols
    cdef int q_index, local_x_index, local_y_index, u_index
    cdef int gu, gsq, tq, sq
    cdef numpy.ndarray[numpy.int32_t, ndim=2] g_block
    cdef numpy.ndarray[numpy.int32_t, ndim=1] s_array
    cdef numpy.ndarray[numpy.int32_t, ndim=1] t_array
    cdef numpy.ndarray[numpy.float32_t, ndim=2] dt
    cdef numpy.ndarray[numpy.int8_t, ndim=2] mask_block

    file_handle, base_mask_path = tempfile.mkstemp()
    os.close(file_handle)
    base_raster_info = pygeoprocessing.get_raster_info(
        base_mask_raster_path_band[0])
    base_nodata = base_raster_info['nodata'][
        base_mask_raster_path_band[1]-1]

    pygeoprocessing.raster_calculator(
        [base_mask_raster_path_band], MaskWrapper(base_nodata),
        base_mask_path,
        gdal.GDT_Byte, NODATA, calc_raster_stats=False)

    base_mask_raster = gdal.OpenEx(base_mask_path)
    base_mask_band = base_mask_raster.GetRasterBand(1)

    n_cols = base_mask_raster.RasterXSize
    n_rows = base_mask_raster.RasterYSize

    file_handle, g_path = tempfile.mkstemp()
    os.close(file_handle)
    raster_info = pygeoprocessing.get_raster_info(
        base_mask_raster_path_band[0])
    pygeoprocessing.new_raster_from_base(
        base_mask_raster_path_band[0], g_path, gdal.GDT_Int32, [NODATA],
        fill_value_list=None)
    g_raster = gdal.OpenEx(g_path, gdal.GA_Update)
    g_band = g_raster.GetRasterBand(1)
    g_band_blocksize = g_band.GetBlockSize()

    numerical_inf = (
        raster_info['raster_size'][0] + raster_info['raster_size'][1])
    # scan 1
    done = False
    block_xsize = raster_info['block_size'][0]
    mask_block = numpy.empty((n_rows, block_xsize), dtype=numpy.int8)
    g_block = numpy.empty((n_rows, block_xsize), dtype=numpy.int32)
    for xoff in numpy.arange(0, n_cols, block_xsize):
        win_xsize = block_xsize
        if xoff + win_xsize > n_cols:
            win_xsize = n_cols - xoff
            mask_block = numpy.empty((n_rows, win_xsize), dtype=numpy.int8)
            g_block = numpy.empty((n_rows, win_xsize), dtype=numpy.int32)
            done = True
        base_mask_band.ReadAsArray(
            xoff=xoff, yoff=0, win_xsize=win_xsize, win_ysize=n_rows,
            buf_obj=mask_block)
        # base case
        g_block[0, :] = (mask_block[0, :] == 0) * numerical_inf
        for row_index in range(1, n_rows):
            for local_x_index in range(win_xsize):
                if mask_block[row_index, local_x_index] == 1:
                    g_block[row_index, local_x_index] = 0
                else:
                    g_block[row_index, local_x_index] = (
                        g_block[row_index-1, local_x_index] + 1)
        for row_index in range(n_rows-2, -1, -1):
            for local_x_index in range(win_xsize):
                if (g_block[row_index+1, local_x_index] <
                        g_block[row_index, local_x_index]):
                    g_block[row_index, local_x_index] = (
                        1 + g_block[row_index+1, local_x_index])
        g_band.WriteArray(g_block, xoff=xoff, yoff=0)
        if done:
            break
    g_band.FlushCache()

    pygeoprocessing.new_raster_from_base(
        base_mask_raster_path_band[0], target_distance_path.encode('utf-8'),
        gdal.GDT_Float32, [NODATA], fill_value_list=None)
    target_distance_raster = gdal.OpenEx(target_distance_path, gdal.GA_Update)
    target_distance_band = target_distance_raster.GetRasterBand(1)

    LOGGER.info('Distance Transform Phase 2')

    s_array = numpy.empty(n_cols, dtype=numpy.int32)
    t_array = numpy.empty(n_cols, dtype=numpy.int32)

    done = False
    block_ysize = g_band_blocksize[1]
    g_block = numpy.empty((block_ysize, n_cols), dtype=numpy.int32)
    dt = numpy.empty((block_ysize, n_cols), dtype=numpy.float32)
    sq = 0  # initialize so compiler doesn't complain
    gsq = 0
    for yoff in numpy.arange(0, n_rows, block_ysize):
        win_ysize = block_ysize
        if yoff + win_ysize >= n_rows:
            win_ysize = n_rows - yoff
            g_block = numpy.empty((win_ysize, n_cols), dtype=numpy.int32)
            dt = numpy.empty((win_ysize, n_cols), dtype=numpy.float32)
            done = True
        g_band.ReadAsArray(
            xoff=0, yoff=yoff, win_xsize=n_cols, win_ysize=win_ysize,
            buf_obj=g_block)
        for local_y_index in range(win_ysize):
            q_index = 0
            s_array[0] = 0
            t_array[0] = 0
            for u_index in range(1, n_cols):
                gu = g_block[local_y_index, u_index]**2
                while (q_index >= 0):
                    tq = t_array[q_index]
                    sq = s_array[q_index]
                    gsq = g_block[local_y_index, sq]**2
                    if ((tq-sq)**2 + gsq <= (tq-u_index)**2 + gu):
                        break
                    q_index -= 1
                if q_index < 0:
                    q_index = 0
                    s_array[0] = u_index
                    sq = u_index
                    gsq = g_block[local_y_index, sq]**2
                else:
                    w = 1 + (
                        u_index**2 - sq**2 + gu - gsq) / (2*(u_index-sq))
                    if w < n_cols:
                        q_index += 1
                        s_array[q_index] = u_index
                        t_array[q_index] = w

            sq = s_array[q_index]
            gsq = g_block[local_y_index, sq]**2
            tq = t_array[q_index]
            for u_index in range(n_cols-1, -1, -1):
                dt[local_y_index, u_index] = (u_index-sq)**2+gsq
                if u_index == tq:
                    q_index -= 1
                    if q_index >= 0:
                        sq = s_array[q_index]
                        gsq = g_block[local_y_index, sq]**2
                        tq = t_array[q_index]

        valid_mask = g_block != NODATA
        dt[valid_mask] = numpy.sqrt(dt[valid_mask])
        dt[~valid_mask] = NODATA
        target_distance_band.WriteArray(dt, xoff=0, yoff=yoff)

        # we do this in the case where the blocksize is many times larger than
        # the raster size so we don't re-loop through the only block
        if done:
            break

    target_distance_band.FlushCache()
    gdal.Dataset.__swig_destroy__(target_distance_raster)
    gdal.Dataset.__swig_destroy__(base_mask_raster)
    gdal.Dataset.__swig_destroy__(g_raster)
    try:
        os.remove(g_path)
    except OSError:
        LOGGER.warn("couldn't remove file %s" % g_path)


@cython.boundscheck(False)
@cython.wraparound(False)
@cython.nonecheck(False)
@cython.cdivision(True)
def calculate_slope(
        base_elevation_raster_path_band, target_slope_path,
        gtiff_creation_options=DEFAULT_GTIFF_CREATION_OPTIONS):
    """Create a percent slope raster from DEM raster.

    Base algorithm is from Zevenbergen & Thorne "Quantitative Analysis of Land
    Surface Topography" 1987 although it has been modified to include the
    diagonal pixels by classic finite difference analysis.

    For the following notation, we define each pixel's DEM value by a letter
    with this spatial scheme:

        abc
        def
        ghi

    Then the slope at e is defined at ([dz/dx]^2 + [dz/dy]^2)^0.5

    Where

    [dz/dx] = ((c+2f+i)-(a+2d+g)/(8*x_cell_size)
    [dz/dy] = ((g+2h+i)-(a+2b+c))/(8*y_cell_size)

    In cases where a cell is nodata, we attempt to use the middle cell inline
    with the direction of differentiation (either in x or y direction).  If
    no inline pixel is defined, we use `e` and multiply the difference by
    2^0.5 to account for the diagonal projection.

    Parameters:
        base_elevation_raster_path_band (string): a path/band tuple to a
            raster of height values. (path_to_raster, band_index)
        target_slope_path (string): path to target slope raster; will be a
            32 bit float GeoTIFF of same size/projection as calculate slope
            with units of percent slope.
        gtiff_creation_options (list or tuple): list of strings that will be
            passed as GDAL "dataset" creation options to the GTIFF driver.

    Returns:
        None
    """
    cdef numpy.npy_float64 a, b, c, d, e, f, g, h, i, dem_nodata
    cdef numpy.npy_float64 x_cell_size, y_cell_size,
    cdef numpy.npy_float64 dzdx_accumulator, dzdy_accumulator
    cdef int row_index, col_index, n_rows, n_cols,
    cdef int x_denom_factor, y_denom_factor, win_xsize, win_ysize
    cdef numpy.ndarray[numpy.npy_float64, ndim=2] dem_array
    cdef numpy.ndarray[numpy.npy_float64, ndim=2] slope_array
    cdef numpy.ndarray[numpy.npy_float64, ndim=2] dzdx_array
    cdef numpy.ndarray[numpy.npy_float64, ndim=2] dzdy_array

    dem_raster = gdal.OpenEx(base_elevation_raster_path_band[0])
    dem_band = dem_raster.GetRasterBand(base_elevation_raster_path_band[1])
    dem_info = pygeoprocessing.get_raster_info(base_elevation_raster_path_band[0])
    dem_nodata = dem_info['nodata'][0]
    x_cell_size, y_cell_size = dem_info['pixel_size']
    n_cols, n_rows = dem_info['raster_size']
    cdef numpy.npy_float64 slope_nodata = numpy.finfo(numpy.float32).min
    pygeoprocessing.new_raster_from_base(
        base_elevation_raster_path_band[0], target_slope_path, gdal.GDT_Float32,
        [slope_nodata], fill_value_list=[float(slope_nodata)],
        gtiff_creation_options=gtiff_creation_options)
    target_slope_raster = gdal.OpenEx(target_slope_path, gdal.GA_Update)
    target_slope_band = target_slope_raster.GetRasterBand(1)

    for block_offset in pygeoprocessing.iterblocks(
            base_elevation_raster_path_band[0], offset_only=True):
        block_offset_copy = block_offset.copy()
        # try to expand the block around the edges if it fits
        x_start = 1
        win_xsize = block_offset['win_xsize']
        x_end = win_xsize+1
        y_start = 1
        win_ysize = block_offset['win_ysize']
        y_end = win_ysize+1

        if block_offset['xoff'] > 0:
            block_offset_copy['xoff'] -= 1
            block_offset_copy['win_xsize'] += 1
            x_start -= 1
        if block_offset['xoff']+win_xsize < n_cols:
            block_offset_copy['win_xsize'] += 1
            x_end += 1
        if block_offset['yoff'] > 0:
            block_offset_copy['yoff'] -= 1
            block_offset_copy['win_ysize'] += 1
            y_start -= 1
        if block_offset['yoff']+win_ysize < n_rows:
            block_offset_copy['win_ysize'] += 1
            y_end += 1

        dem_array = numpy.empty(
            (win_ysize+2, win_xsize+2),
            dtype=numpy.float64)
        dem_array[:] = dem_nodata
        slope_array = numpy.empty(
            (win_ysize, win_xsize),
            dtype=numpy.float64)
        dzdx_array = numpy.empty(
            (win_ysize, win_xsize),
            dtype=numpy.float64)
        dzdy_array = numpy.empty(
            (win_ysize, win_xsize),
            dtype=numpy.float64)

        dem_band.ReadAsArray(
            buf_obj=dem_array[y_start:y_end, x_start:x_end],
            **block_offset_copy)

        for row_index in range(1, win_ysize+1):
            for col_index in range(1, win_xsize+1):
                # Notation of the cell below comes from the algorithm
                # description, cells are arraged as follows:
                # abc
                # def
                # ghi
                e = dem_array[row_index, col_index]
                if e == dem_nodata:
                    # we use dzdx as a guard below, no need to set dzdy
                    dzdx_array[row_index-1, col_index-1] = slope_nodata
                    continue
                dzdx_accumulator = 0.0
                dzdy_accumulator = 0.0
                x_denom_factor = 0
                y_denom_factor = 0
                a = dem_array[row_index-1, col_index-1]
                b = dem_array[row_index-1, col_index]
                c = dem_array[row_index-1, col_index+1]
                d = dem_array[row_index, col_index-1]
                f = dem_array[row_index, col_index+1]
                g = dem_array[row_index+1, col_index-1]
                h = dem_array[row_index+1, col_index]
                i = dem_array[row_index+1, col_index+1]

                # a - c direction
                if a != dem_nodata and c != dem_nodata:
                    dzdx_accumulator += a - c
                    x_denom_factor += 2
                elif a != dem_nodata and b != dem_nodata:
                    dzdx_accumulator += a - b
                    x_denom_factor += 1
                elif b != dem_nodata and c != dem_nodata:
                    dzdx_accumulator += b - c
                    x_denom_factor += 1
                elif a != dem_nodata:
                    dzdx_accumulator += (a - e) * 2**0.5
                    x_denom_factor += 1
                elif c != dem_nodata:
                    dzdx_accumulator += (e - c) * 2**0.5
                    x_denom_factor += 1

                # d - f direction
                if d != dem_nodata and f != dem_nodata:
                    dzdx_accumulator += 2 * (d - f)
                    x_denom_factor += 4
                elif d != dem_nodata:
                    dzdx_accumulator += 2 * (d - e)
                    x_denom_factor += 2
                elif f != dem_nodata:
                    dzdx_accumulator += 2 * (e - f)
                    x_denom_factor += 2

                # g - i direction
                if g != dem_nodata and i != dem_nodata:
                    dzdx_accumulator += g - i
                    x_denom_factor += 2
                elif g != dem_nodata and h != dem_nodata:
                    dzdx_accumulator += g - h
                    x_denom_factor += 1
                elif h != dem_nodata and i != dem_nodata:
                    dzdx_accumulator += h - i
                    x_denom_factor += 1
                elif g != dem_nodata:
                    dzdx_accumulator += (g - e) * 2**0.5
                    x_denom_factor += 1
                elif i != dem_nodata:
                    dzdx_accumulator += (e - i) * 2**0.5
                    x_denom_factor += 1

                # a - g direction
                if a != dem_nodata and g != dem_nodata:
                    dzdy_accumulator += a - g
                    y_denom_factor += 2
                elif a != dem_nodata and d != dem_nodata:
                    dzdy_accumulator += a - d
                    y_denom_factor += 1
                elif d != dem_nodata and g != dem_nodata:
                    dzdy_accumulator += d - g
                    y_denom_factor += 1
                elif a != dem_nodata:
                    dzdy_accumulator += (a - e) * 2**0.5
                    y_denom_factor += 1
                elif g != dem_nodata:
                    dzdy_accumulator += (e - g) * 2**0.5
                    y_denom_factor += 1

                # b - h direction
                if b != dem_nodata and h != dem_nodata:
                    dzdy_accumulator += 2 * (b - h)
                    y_denom_factor += 4
                elif b != dem_nodata:
                    dzdy_accumulator += 2 * (b - e)
                    y_denom_factor += 2
                elif h != dem_nodata:
                    dzdy_accumulator += 2 * (e - h)
                    y_denom_factor += 2

                # c - i direction
                if c != dem_nodata and i != dem_nodata:
                    dzdy_accumulator += c - i
                    y_denom_factor += 2
                elif c != dem_nodata and f != dem_nodata:
                    dzdy_accumulator += c - f
                    y_denom_factor += 1
                elif f != dem_nodata and i != dem_nodata:
                    dzdy_accumulator += f - i
                    y_denom_factor += 1
                elif c != dem_nodata:
                    dzdy_accumulator += (c - e) * 2**0.5
                    y_denom_factor += 1
                elif i != dem_nodata:
                    dzdy_accumulator += (e - i) * 2**0.5
                    y_denom_factor += 1

                if x_denom_factor != 0:
                    dzdx_array[row_index-1, col_index-1] = (
                        dzdx_accumulator / (x_denom_factor * x_cell_size))
                else:
                    dzdx_array[row_index-1, col_index-1] = 0.0
                if y_denom_factor != 0:
                    dzdy_array[row_index-1, col_index-1] = (
                        dzdy_accumulator / (y_denom_factor * y_cell_size))
                else:
                    dzdy_array[row_index-1, col_index-1] = 0.0
        valid_mask = dzdx_array != slope_nodata
        slope_array[:] = slope_nodata
        # multiply by 100 for percent output
        slope_array[valid_mask] = 100.0 * numpy.sqrt(
            dzdx_array[valid_mask]**2 + dzdy_array[valid_mask]**2)
        target_slope_band.WriteArray(
            slope_array, xoff=block_offset['xoff'],
            yoff=block_offset['yoff'])

    dem_band = None
    target_slope_band = None
    gdal.Dataset.__swig_destroy__(dem_raster)
    gdal.Dataset.__swig_destroy__(target_slope_raster)
    dem_raster = None
    target_slope_raster = None


@cython.boundscheck(False)
def stats_worker(stats_work_queue, exception_queue):
    """Worker to calculate continuous min, max, mean and standard deviation.

    Parameters:
        stats_work_queue (Queue): a queue of 1D numpy arrays or None. If
            None, function puts a (min, max, mean, stddev) tuple to the
            queue and quits.

    Returns:
        None

    """
    cdef numpy.ndarray[numpy.float64_t, ndim=1] block
    cdef double M_local = 0.0
    cdef double S_local = 0.0
    cdef double min_value = 0.0
    cdef double max_value = 0.0
    cdef double x = 0.0
    cdef int i, n_elements
    cdef long long n = 0L
    payload = None

    try:
        while True:
            payload = stats_work_queue.get()
            if payload is None:
                LOGGER.debug('payload is None, terminating')
                break
            block = payload.astype(numpy.float64)
            n_elements = block.size
            with nogil:
                for i in range(n_elements):
                    n = n + 1
                    x = block[i]
                    if n <= 0:
                        with gil:
                            LOGGER.error('invalid value for n %s' % n)
                    if n == 1:
                        M_local = x
                        S_local = 0.0
                        min_value = x
                        max_value = x
                    else:
                        M_last = M_local
                        M_local = M_local+(x - M_local)/<double>(n)
                        S_local = S_local+(x-M_last)*(x-M_local)
                        if x < min_value:
                            min_value = x
                        elif x > max_value:
                            max_value = x

        if n > 0:
            stats_work_queue.put(
                (min_value, max_value, M_local, (S_local / <double>n) ** 0.5))
        else:
            LOGGER.warn(
                "No valid pixels were received, sending None.")
            stats_work_queue.put(None)
    except Exception as e:
        LOGGER.exception(
            "exception %s %s %s %s %s", x, M_local, S_local, n, payload)
        exception_queue.put(e)
        while not stats_work_queue.empty():
            stats_work_queue.get()
        raise
