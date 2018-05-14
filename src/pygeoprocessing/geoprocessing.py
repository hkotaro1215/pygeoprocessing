"""A collection of GDAL dataset and raster utilities."""
import types
import logging
import os
import shutil
import functools
import math
import exceptions
import heapq
import time
import tempfile
import uuid
import distutils.version
import multiprocessing
import multiprocessing.pool
import threading
import Queue

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

from osgeo import gdal
from osgeo import osr
from osgeo import ogr
import numpy
import numpy.ma
import scipy.interpolate
import scipy.sparse
import scipy.signal
import scipy.ndimage
import scipy.signal.signaltools
import shapely.wkt
import shapely.ops
from shapely import speedups
import shapely.prepared
from . import geoprocessing_core

LOGGER = logging.getLogger(__name__)

_MAX_TIMEOUT = 5.0
_LOGGING_PERIOD = 5.0  # min 5.0 seconds per update log message for the module
_DEFAULT_GTIFF_CREATION_OPTIONS = (
    'TILED=YES', 'BIGTIFF=YES', 'COMPRESS=LZW',
    'BLOCKXSIZE=256', 'BLOCKYSIZE=256')
_LARGEST_ITERBLOCK = 2**16  # largest block for iterblocks to read in cells

# A dictionary to map the resampling method input string to the gdal type
_RESAMPLE_DICT = {
    "near": gdal.GRA_NearestNeighbour,
    "bilinear": gdal.GRA_Bilinear,
    "cubic": gdal.GRA_Cubic,
    "cubic_spline": gdal.GRA_CubicSpline,
    "lanczos": gdal.GRA_Lanczos,
    'mode': gdal.GRA_Mode,
    'average': gdal.GRA_Average,
}


# GDAL 2.2.3 added a couple of useful interpolation values.
if (distutils.version.LooseVersion(gdal.__version__) >=
        distutils.version.LooseVersion('2.2.3')):
    _RESAMPLE_DICT.update({
        'max': gdal.GRA_Max,
        'min': gdal.GRA_Min,
        'med': gdal.GRA_Med,
        'q1': gdal.GRA_Q1,
        'q3': gdal.GRA_Q3,
    })


def raster_calculator(
        base_raster_path_band_list, local_op, target_raster_path,
        datatype_target, nodata_target,
        gtiff_creation_options=_DEFAULT_GTIFF_CREATION_OPTIONS,
        calc_raster_stats=True,
        largest_block=_LARGEST_ITERBLOCK):
    """Apply local a raster operation on a stack of rasters.

    This function applies a user defined function across a stack of
    rasters' pixel stack. The rasters in `base_raster_path_band_list` must be
    spatially aligned and have the same cell sizes.

    Parameters:
        base_raster_path_band_list (list): a list of (str, int) tuples where
            the strings are raster paths, and ints are band indexes.  The
            rasters in this list must have the same raster size so pixel
            stacks align.
        local_op (function) a function that must take in as many arguments as
            there are elements in `base_raster_path_band_list`.  The will be
            in the same order as the rasters in arguments
            can be treated as parallel memory blocks from the original
            rasters though the function is a parallel
            paradigm and does not express the spatial position of the pixels
            in question at the time of the call.
        target_raster_path (string): the path of the output raster.  The
            projection, size, and cell size will be the same as the rasters
            in `base_raster_path_band_list`.
        datatype_target (gdal datatype; int): the desired GDAL output type of
            the target raster.
        nodata_target (numerical value): the desired nodata value of the
            target raster.
        gtiff_creation_options (list): this is an argument list that will be
            passed to the GTiff driver.  Useful for blocksizes, compression,
            and more.
        calc_raster_stats (boolean): If True, calculates and sets raster
            statistics (min, max, mean, and stdev) for target raster.
        largest_block (int): Attempts to internally iterate over raster blocks
            with this many elements.  Useful in cases where the blocksize is
            relatively small, memory is available, and the function call
            overhead dominates the iteration.  Defaults to 2**20.  A value of
            anything less than the original blocksize of the raster will
            result in blocksizes equal to the original size.

    Returns:
        None

    Raises:
        ValueError: invalid input provided

    """
    # It's a common error to not pass in path/band tuples, so check for that
    # and report error if so
    bad_raster_path_list = False
    if not isinstance(base_raster_path_band_list, (list, tuple)):
        bad_raster_path_list = True
    else:
        for value in base_raster_path_band_list:
            if not _is_raster_path_band_formatted(value):
                bad_raster_path_list = True
                break
    if bad_raster_path_list:
        raise ValueError(
            "Expected a list of path / integer band tuples for "
            "`base_raster_path_band_list`, instead got: %s" %
            str(base_raster_path_band_list))

    not_found_paths = []
    gdal.PushErrorHandler('CPLQuietErrorHandler')
    for path, _ in base_raster_path_band_list:
        if gdal.OpenEx(path) is None:
            not_found_paths.append(path)
    gdal.PopErrorHandler()

    if len(not_found_paths) != 0:
        raise exceptions.ValueError(
            "The following files were expected but do not exist on the "
            "filesystem: " + str(not_found_paths))

    if target_raster_path in [x[0] for x in base_raster_path_band_list]:
        raise ValueError(
            "%s is used as a target path, but it is also in the base input "
            "path list %s" % (
                target_raster_path, str(base_raster_path_band_list)))

    raster_info_list = [
        get_raster_info(path_band[0])
        for path_band in base_raster_path_band_list]
    geospatial_info_set = set()
    for raster_info in raster_info_list:
        geospatial_info_set.add(raster_info['raster_size'])
    if len(geospatial_info_set) > 1:
        raise ValueError(
            "Input Rasters are not the same dimensions. The "
            "following raster are not identical %s" % str(
                geospatial_info_set))

    base_raster_info = get_raster_info(base_raster_path_band_list[0][0])

    new_raster_from_base(
        base_raster_path_band_list[0][0], target_raster_path, datatype_target,
        [nodata_target], gtiff_creation_options=gtiff_creation_options)

    try:
        n_cols, n_rows = base_raster_info['raster_size']
        last_time = time.time()

        # load base rasters and bands
        base_raster_list = [
            gdal.OpenEx(path_band[0])
            for path_band in base_raster_path_band_list]
        base_band_list = [
            raster.GetRasterBand(index) for raster, (_, index) in zip(
                base_raster_list, base_raster_path_band_list)]

        local_op_work_queue = Queue.Queue(2)
        write_block_queue = Queue.Queue(2)
        exception_queue = Queue.Queue()

        if calc_raster_stats:
            # if this queue is used to send computed valid blocks of
            # the raster to an incremental statistics calculator worker
            stats_worker_queue = Queue.Queue(2)
        else:
            stats_worker_queue = None

        # the write worker takes the result of the call to `local_op` and
        # writes it to the raster. It is not possible to parallel write to
        # most raster types, so this gives slightly better performance than
        # a single process doing both the read/computation and writing
        # especially if writing to a compressed raster that needs extra
        # computation.
        LOGGER.debug('starting write_worker')
        write_worker_thread = threading.Thread(
            target=_write_block_worker,
            args=(
                write_block_queue, target_raster_path, exception_queue))
        write_worker_thread.start()
        LOGGER.debug('started write_worker  %s', write_worker_thread)

        if calc_raster_stats:
            # To avoid doing two passes on the raster to calculate standard
            # deviation, we implement a continuous statistics calculation
            # as the raster is computed. This computational effort is high
            # and benefits from running in parallel. This queue and worker
            # takes a valid block of a raster and incrementally calculates
            # the raster's statistics. When `None` is pushed to the queue
            # the worker will finish and return a (min, max, mean, std)
            # tuple.
            LOGGER.debug('starting stats_worker')
            stats_worker_thread = threading.Thread(
                target=geoprocessing_core.stats_worker,
                args=(stats_worker_queue, exception_queue))
            stats_worker_thread.start()
            LOGGER.debug('started stats_worker %s', stats_worker_thread)

        LOGGER.debug('stats worker queue %s %s', stats_worker_queue, calc_raster_stats)

        pixels_processed = 0
        n_pixels = n_cols * n_rows

        local_op_thread = threading.Thread(
            target=_local_op_worker,
            args=(
                local_op, nodata_target, local_op_work_queue,
                write_block_queue, stats_worker_queue, exception_queue))
        local_op_thread.start()

        # iterate over each block and calculate local_op
        for block_offset in iterblocks(
                base_raster_path_band_list[0][0], offset_only=True,
                largest_block=largest_block):

            # read input blocks
            blocksize = (block_offset['win_ysize'], block_offset['win_xsize'])
            raster_blocks = [
                numpy.zeros(blocksize, dtype=_gdal_to_numpy_type(band))
                for band in base_band_list]
            for dataset_index in xrange(len(base_band_list)):
                band_data = block_offset.copy()
                band_data['buf_obj'] = raster_blocks[dataset_index]
                base_band_list[dataset_index].ReadAsArray(**band_data)

            if exception_queue.empty():
                local_op_work_queue.put(
                    (block_offset, raster_blocks))
            else:
                LOGGER.error("Exception queue is not empty, quitting.")
                break

            pixels_processed += blocksize[0] * blocksize[1]
            last_time = _invoke_timed_callback(
                last_time, lambda: LOGGER.info(
                    '%.2f%% complete',
                    float(pixels_processed) / n_pixels * 100.0),
                _LOGGING_PERIOD)

        local_op_work_queue.put(None)
        local_op_thread.join(_MAX_TIMEOUT)
        if not exception_queue.empty():
            LOGGER.error("Exception queue is not empty, raising exception.")
            raise exception_queue.get(True, _MAX_TIMEOUT)
        LOGGER.info('100.00%% complete')

        # push `None` to work queues
        LOGGER.info('signaling write worker to shut down')
        write_block_queue.put(None, True, _MAX_TIMEOUT)
        if calc_raster_stats:
            LOGGER.info('signaling stats worker to shut down')
            stats_worker_queue.put(None, True, _MAX_TIMEOUT)

        # Making sure the band and dataset is flushed and not in memory before
        # adding stats
        LOGGER.info("waiting for write worker to terminate")
        write_worker_thread.join(_MAX_TIMEOUT)
        LOGGER.info("write_worker terminated.")

        if calc_raster_stats:
            LOGGER.info("Waiting for raster stats worker result.")
            stats_worker_thread.join(_MAX_TIMEOUT)
            payload = stats_worker_queue.get(True, _MAX_TIMEOUT)
            LOGGER.info("Got %s from stats worker", payload)
            if payload is not None:
                target_min, target_max, target_mean, target_stddev = payload
                target_raster = gdal.OpenEx(
                    target_raster_path, gdal.GA_Update | gdal.OF_RASTER)
                target_band = target_raster.GetRasterBand(1)
                target_band.SetStatistics(
                    float(target_min), float(target_max), float(target_mean),
                    float(target_stddev))
                target_band.FlushCache()
                target_band = None
                gdal.Dataset.__swig_destroy__(target_raster)
    except:
        LOGGER.exception('Exception encountered.')
        raise
    finally:
        if calc_raster_stats and stats_worker_thread.is_alive():
            stats_worker_queue.put(None, True, _MAX_TIMEOUT)
            stats_worker_thread.join(_MAX_TIMEOUT)
            LOGGER.debug('stats_worker terminated')

        if write_worker_thread.is_alive():
            write_block_queue.put(None, True, _MAX_TIMEOUT)
            write_worker_thread.join(_MAX_TIMEOUT)
            LOGGER.debug("write_worker terminated.")


def align_and_resize_raster_stack(
        base_raster_path_list, target_raster_path_list, resample_method_list,
        target_pixel_size, bounding_box_mode, base_vector_path_list=None,
        raster_align_index=None,
        gtiff_creation_options=_DEFAULT_GTIFF_CREATION_OPTIONS):
    """Generate rasters from a base such that they align geospatially.

    This function resizes base rasters that are in the same geospatial
    projection such that the result is an aligned stack of rasters that have
    the same cell size, dimensions, and bounding box. This is achieved by
    clipping or resizing the rasters to intersected, unioned, or equivocated
    bounding boxes of all the raster and vector input.

    Parameters:
        base_raster_path_list (list): a list of base raster paths that will
            be transformed and will be used to determine the target bounding
            box.
        target_raster_path_list (list): a list of raster paths that will be
            created to one-to-one map with `base_raster_path_list` as aligned
            versions of those original rasters.
        resample_method_list (list): a list of resampling methods which
            one to one map each path in `base_raster_path_list` during
            resizing.  Each element must be one of
            "near|bilinear|cubic|cubic_spline|lanczos|mode".
        target_pixel_size (tuple): the target raster's x and y pixel size
            example: [30, -30].
        bounding_box_mode (string): one of "union", "intersection", or
            a list of floats of the form [minx, miny, maxx, maxy].  Depending
            on the value, output extents are defined as the union,
            intersection, or the explicit bounding box.
        base_vector_path_list (list): a list of base vector paths whose
            bounding boxes will be used to determine the final bounding box
            of the raster stack if mode is 'union' or 'intersection'.  If mode
            is 'bb=[...]' then these vectors are not used in any calculation.
        raster_align_index (int): indicates the index of a
            raster in `base_raster_path_list` that the target rasters'
            bounding boxes pixels should align with.  This feature allows
            rasters whose raster dimensions are the same, but bounding boxes
            slightly shifted less than a pixel size to align with a desired
            grid layout.  If `None` then the bounding box of the target
            rasters is calculated as the precise intersection, union, or
            bounding box.
        gtiff_creation_options (list): list of strings that will be passed
            as GDAL "dataset" creation options to the GTIFF driver, or ignored
            if None.

    Returns:
        None

    """
    # make sure that the input lists are of the same length
    list_lengths = [
        len(base_raster_path_list), len(target_raster_path_list),
        len(resample_method_list)]
    if len(set(list_lengths)) != 1:
        raise ValueError(
            "base_raster_path_list, target_raster_path_list, and "
            "resample_method_list must be the same length "
            " current lengths are %s" % (str(list_lengths)))

    # we can accept 'union', 'intersection', or a 4 element list/tuple
    if bounding_box_mode not in ["union", "intersection"] and (
            not isinstance(bounding_box_mode, (list, tuple)) or
            len(bounding_box_mode) != 4):
        raise ValueError("Unknown bounding_box_mode %s" % (
            str(bounding_box_mode)))

    n_rasters = len(base_raster_path_list)
    if ((raster_align_index is not None) and
            ((raster_align_index < 0) or (raster_align_index >= n_rasters))):
        raise ValueError(
            "Alignment index is out of bounds of the datasets index: %s"
            " n_elements %s" % (raster_align_index, n_rasters))

    raster_info_list = [
        get_raster_info(path) for path in base_raster_path_list]
    if base_vector_path_list is not None:
        vector_info_list = [
            get_vector_info(path) for path in base_vector_path_list]
    else:
        vector_info_list = []

    # get the literal or intersecting/unioned bounding box
    if isinstance(bounding_box_mode, (list, tuple)):
        target_bounding_box = bounding_box_mode
    else:
        # either intersection or union
        target_bounding_box = reduce(
            functools.partial(_merge_bounding_boxes, mode=bounding_box_mode),
            [info['bounding_box'] for info in
             (raster_info_list + vector_info_list)])

    if bounding_box_mode == "intersection" and (
            target_bounding_box[0] > target_bounding_box[2] or
            target_bounding_box[1] > target_bounding_box[3]):
        raise ValueError("The rasters' and vectors' intersection is empty "
                         "(not all rasters and vectors touch each other).")

    if raster_align_index >= 0:
        # bounding box needs alignment
        align_bounding_box = (
            raster_info_list[raster_align_index]['bounding_box'])
        align_pixel_size = (
            raster_info_list[raster_align_index]['pixel_size'])
        # adjust bounding box so lower left corner aligns with a pixel in
        # raster[raster_align_index]
        for index in [0, 1]:
            n_pixels = int(
                (target_bounding_box[index] - align_bounding_box[index]) /
                float(align_pixel_size[index]))
            target_bounding_box[index] = (
                n_pixels * align_pixel_size[index] +
                align_bounding_box[index])

    # using half the number of CPUs because in practice `warp_raster` seems
    # to use 2 cores.
    n_workers = max(min(multiprocessing.cpu_count(), n_rasters) / 2, 1)

    if n_workers > 1:
        # We could use multiple processes to take advantage of the
        # parallelization offered.
        LOGGER.info(
            "n_workers > 1 (%d) so starting a processes pool.", n_workers)
        try:
            worker_pool = multiprocessing.Pool(n_workers)
        except RuntimeError:
            LOGGER.warning(
                "Runtime error when starting multiprocessing pool. This is "
                "likely because this process is running on Windows and the "
                "main entry point is not wrapped in an `if __name__ == "
                "'__main__': block. Returning from this function to attempt "
                "to recover.")
            return
    else:
        LOGGER.info("n_workers == 1 so a threadpool is sufficient")
        worker_pool = multiprocessing.pool.ThreadPool(n_workers)

    if HAS_PSUTIL:
        parent = psutil.Process()
        parent.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        for child in parent.children():
            try:
                child.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            except psutil.NoSuchProcess:
                LOGGER.warn(
                    "NoSuchProcess exception encountered when trying "
                    "to nice %s. This might be a bug in `psutil` so "
                    "it should be okay to ignore.")

    result_list = []
    for index, (base_path, target_path, resample_method) in enumerate(zip(
            base_raster_path_list, target_raster_path_list,
            resample_method_list)):
        result = worker_pool.apply_async(
            func=warp_raster, args=(
                base_path, target_pixel_size, target_path, resample_method),
            kwds={
                'target_bb': target_bounding_box,
                'gtiff_creation_options': gtiff_creation_options,
                })
        result_list.append(result)

    try:
        for result in result_list:
            result.get()
    except:
        worker_pool.terminate()
        LOGGER.exception("Exception occurred in worker")
        raise

    LOGGER.info("aligned all %d rasters.", n_rasters)


def calculate_raster_stats(raster_path):
    """Calculate and set min, max, stdev, and mean for all bands in raster.

    Parameters:
        raster_path (string): a path to a GDAL raster raster that will be
            modified by having its band statistics set

    Returns:
        None

    """
    raster = gdal.OpenEx(raster_path, gdal.GA_Update)
    raster_properties = get_raster_info(raster_path)
    for band_index in xrange(raster.RasterCount):
        target_min = None
        target_max = None
        target_n = 0
        target_sum = 0.0
        for _, target_block in iterblocks(
                raster_path, band_index_list=[band_index+1]):
            nodata_target = raster_properties['nodata'][band_index]
            # guard against an undefined nodata target
            valid_mask = numpy.ones(target_block.shape, dtype=bool)
            if nodata_target is not None:
                valid_mask[:] = target_block != nodata_target
            valid_block = target_block[valid_mask]
            if valid_block.size == 0:
                continue
            if target_min is None:
                # initialize first min/max
                target_min = target_max = valid_block[0]
            target_sum += numpy.sum(valid_block)
            target_min = min(numpy.min(valid_block), target_min)
            target_max = max(numpy.max(valid_block), target_max)
            target_n += valid_block.size

        if target_min is not None:
            target_mean = target_sum / float(target_n)
            stdev_sum = 0.0
            for _, target_block in iterblocks(
                    raster_path, band_index_list=[band_index+1]):
                # guard against an undefined nodata target
                valid_mask = numpy.ones(target_block.shape, dtype=bool)
                if nodata_target is not None:
                    valid_mask = target_block != nodata_target
                valid_block = target_block[valid_mask]
                stdev_sum += numpy.sum((valid_block - target_mean) ** 2)
            target_stddev = (stdev_sum / float(target_n)) ** 0.5

            target_band = raster.GetRasterBand(band_index+1)
            target_band.SetStatistics(
                float(target_min), float(target_max), float(target_mean),
                float(target_stddev))
            target_band = None
        else:
            LOGGER.warn(
                "Stats not calculated for %s band %d since no non-nodata "
                "pixels were found.", raster_path, band_index+1)
    raster = None


def new_raster_from_base(
        base_path, target_path, datatype, band_nodata_list,
        fill_value_list=None, n_rows=None, n_cols=None,
        gtiff_creation_options=_DEFAULT_GTIFF_CREATION_OPTIONS):
    """Create new GeoTIFF by coping spatial reference/geotransform of base.

    A convenience function to simplify the creation of a new raster from the
    basis of an existing one.  Depending on the input mode, one can create
    a new raster of the same dimensions, geotransform, and georeference as
    the base.  Other options are provided to change the raster dimensions,
    number of bands, nodata values, data type, and core GeoTIFF creation
    options.

    Parameters:
        base_path (string): path to existing raster.
        target_path (string): path to desired target raster.
        datatype: the pixel datatype of the output raster, for example
            gdal.GDT_Float32.  See the following header file for supported
            pixel types:
            http://www.gdal.org/gdal_8h.html#22e22ce0a55036a96f652765793fb7a4
        band_nodata_list (list): list of nodata values, one for each band, to
            set on target raster.  If value is 'None' the nodata value is not
            set for that band.  The number of target bands is inferred from
            the length of this list.
        fill_value_list (list): list of values to fill each band with. If None,
            no filling is done.
        n_rows (int): if not None, defines the number of target raster rows.
        n_cols (int): if not None, defines the number of target raster
            columns.
        gtiff_creation_options: a list of dataset options that gets
            passed to the gdal creation driver, overrides defaults

    Returns:
        None

    """
    base_raster = gdal.OpenEx(base_path)
    if n_rows is None:
        n_rows = base_raster.RasterYSize
    if n_cols is None:
        n_cols = base_raster.RasterXSize
    driver = gdal.GetDriverByName('GTiff')

    local_gtiff_creation_options = list(gtiff_creation_options)
    # PIXELTYPE is sometimes used to define signed vs. unsigned bytes and
    # the only place that is stored is in the IMAGE_STRUCTURE metadata
    # copy it over if it exists and it not already defined by the input
    # creation options. It's okay to get this info from the first band since
    # all bands have the same datatype
    base_band = base_raster.GetRasterBand(1)
    metadata = base_band.GetMetadata('IMAGE_STRUCTURE')
    if 'PIXELTYPE' in metadata and not any(
            ['PIXELTYPE' in option for option in
             local_gtiff_creation_options]):
        local_gtiff_creation_options.append(
            'PIXELTYPE=' + metadata['PIXELTYPE'])

    block_size = base_band.GetBlockSize()
    # It's not clear how or IF we can determine if the output should be
    # striped or tiled.  Here we leave it up to the default inputs or if its
    # obviously not striped we tile.
    if not any(
            ['TILED' in option for option in local_gtiff_creation_options]):
        # TILED not set, so lets try to set it to a reasonable value
        if block_size[0] != n_cols:
            # if x block is not the width of the raster it *must* be tiled
            # otherwise okay if it's striped or tiled
            local_gtiff_creation_options.append('TILED=YES')

    if not any(
            ['BLOCK' in option for option in local_gtiff_creation_options]):
        # not defined, so lets copy what we know from the current raster
        local_gtiff_creation_options.extend([
            'BLOCKXSIZE=%d' % block_size[0],
            'BLOCKYSIZE=%d' % block_size[1]])

    # make target directory if it doesn't exist
    try:
        os.makedirs(os.path.dirname(target_path))
    except OSError:
        pass

    base_band = None
    n_bands = len(band_nodata_list)
    target_raster = driver.Create(
        target_path.encode('utf-8'), n_cols, n_rows, n_bands, datatype,
        options=gtiff_creation_options)
    target_raster.SetProjection(base_raster.GetProjection())
    target_raster.SetGeoTransform(base_raster.GetGeoTransform())
    base_raster = None

    for index, nodata_value in enumerate(band_nodata_list):
        if nodata_value is None:
            continue
        target_band = target_raster.GetRasterBand(index + 1)
        try:
            target_band.SetNoDataValue(nodata_value.item())
        except AttributeError:
            target_band.SetNoDataValue(nodata_value)

    target_raster.FlushCache()
    last_time = time.time()
    pixels_processed = 0
    n_pixels = n_cols * n_rows
    if fill_value_list is not None:
        for index, fill_value in enumerate(fill_value_list):
            if fill_value is None:
                continue
            target_band = target_raster.GetRasterBand(index + 1)
            # some rasters are very large and a fill can appear to cause
            # computation to hang. This block, though possibly slightly less
            # efficient than `band.Fill` will give real-time feedback about
            # how the fill is progressing.
            for offsets in iterblocks(target_path, offset_only=True):
                fill_array = numpy.empty(
                    (offsets['win_ysize'], offsets['win_xsize']))
                pixels_processed += (
                    offsets['win_ysize'] * offsets['win_xsize'])
                fill_array[:] = fill_value
                target_band.WriteArray(
                    fill_array, offsets['xoff'], offsets['yoff'])

                last_time = _invoke_timed_callback(
                    last_time, lambda: LOGGER.debug(
                        '%.2f%% complete',
                        float(pixels_processed) / n_pixels * 100.0),
                    _LOGGING_PERIOD)
            target_band = None
    target_raster = None


def create_raster_from_vector_extents(
        base_vector_path, target_raster_path, target_pixel_size,
        target_pixel_type, target_nodata, fill_value=None,
        gtiff_creation_options=_DEFAULT_GTIFF_CREATION_OPTIONS):
    """Create a blank raster based on a vector file extent.

    Parameters:
        base_vector_path (string): path to vector shapefile to base the
            bounding box for the target raster.
        target_raster_path (string): path to location of generated geotiff;
            the upper left hand corner of this raster will be aligned with the
            bounding box of the source vector and the extent will be exactly
            equal or contained the source vector's bounding box depending on
            whether the pixel size divides evenly into the source bounding
            box; if not coordinates will be rounded up to contain the original
            extent.
        target_pixel_size (list): the x/y pixel size as a list [30.0, -30.0]
        target_pixel_type (int): gdal GDT pixel type of target raster
        target_nodata: target nodata value
        fill_value (int/float): value to fill in the target raster; no fill if
            value is None
        gtiff_creation_options (list): this is an argument list that will be
            passed to the GTiff driver.  Useful for blocksizes, compression,
            and more.

    Returns:
        None

    """
    # Determine the width and height of the tiff in pixels based on the
    # maximum size of the combined envelope of all the features
    vector = gdal.OpenEx(base_vector_path)
    shp_extent = None
    for layer_index in xrange(vector.GetLayerCount()):
        layer = vector.GetLayer(layer_index)
        for feature in layer:
            try:
                # envelope is [xmin, xmax, ymin, ymax]
                feature_extent = feature.GetGeometryRef().GetEnvelope()
                if shp_extent is None:
                    shp_extent = list(feature_extent)
                else:
                    # expand bounds of current bounding box to include that
                    # of the newest feature
                    shp_extent = [
                        f(shp_extent[index], feature_extent[index])
                        for index, f in enumerate([min, max, min, max])]
            except AttributeError as error:
                # For some valid OGR objects the geometry can be undefined
                # since it's valid to have a NULL entry in the attribute table
                # this is expressed as a None value in the geometry reference
                # this feature won't contribute
                LOGGER.warn(error)

    # round up on the rows and cols so that the target raster encloses the
    # base vector
    n_cols = int(numpy.ceil(
        abs((shp_extent[1] - shp_extent[0]) / target_pixel_size[0])))
    n_rows = int(numpy.ceil(
        abs((shp_extent[3] - shp_extent[2]) / target_pixel_size[1])))

    driver = gdal.GetDriverByName('GTiff')
    n_bands = 1
    raster = driver.Create(
        target_raster_path, n_cols, n_rows, n_bands, target_pixel_type,
        options=gtiff_creation_options)
    raster.GetRasterBand(1).SetNoDataValue(target_nodata)

    # Set the transform based on the upper left corner and given pixel
    # dimensions
    if target_pixel_size[0] < 0:
        x_source = shp_extent[1]
    else:
        x_source = shp_extent[0]
    if target_pixel_size[1] < 0:
        y_source = shp_extent[3]
    else:
        y_source = shp_extent[2]
    raster_transform = [
        x_source, target_pixel_size[0], 0.0,
        y_source, 0.0, target_pixel_size[1]]
    raster.SetGeoTransform(raster_transform)

    # Use the same projection on the raster as the shapefile
    raster.SetProjection(vector.GetLayer(0).GetSpatialRef().ExportToWkt())

    # Initialize everything to nodata
    if fill_value is not None:
        band = raster.GetRasterBand(1)
        band.Fill(fill_value)
        band.FlushCache()
        band = None
    raster = None


def interpolate_points(
        base_vector_path, vector_attribute_field, target_raster_path_band,
        interpolation_mode):
    """Interpolate point values onto an existing raster.

    Parameters:
        base_vector_path (string): path to a shapefile that contains point
            vector layers.
        vector_attribute_field (field): a string in the vector referenced at
            `base_vector_path` that refers to a numeric value in the
            vector's attribute table.  This is the value that will be
            interpolated across the raster.
        target_raster_path_band (tuple): a path/band number tuple to an
            existing raster which likely intersects or is nearby the source
            vector. The band in this raster will take on the interpolated
            numerical values  provided at each point.
        interpolation_mode (string): the interpolation method to use for
            scipy.interpolate.griddata, one of 'linear', near', or 'cubic'.

    Returns:
       None

    """
    source_vector = gdal.OpenEx(base_vector_path)
    point_list = []
    value_list = []
    for layer_index in xrange(source_vector.GetLayerCount()):
        layer = source_vector.GetLayer(layer_index)
        for point_feature in layer:
            value = point_feature.GetField(vector_attribute_field)
            # Add in the numpy notation which is row, col
            # Here the point geometry is in the form x, y (col, row)
            geometry = point_feature.GetGeometryRef()
            point = geometry.GetPoint()
            point_list.append([point[1], point[0]])
            value_list.append(value)

    point_array = numpy.array(point_list)
    value_array = numpy.array(value_list)

    target_raster = gdal.OpenEx(target_raster_path_band[0], gdal.GA_Update)
    band = target_raster.GetRasterBand(target_raster_path_band[1])
    nodata = band.GetNoDataValue()
    geotransform = target_raster.GetGeoTransform()
    for offsets in iterblocks(
            target_raster_path_band[0], offset_only=True):
        grid_y, grid_x = numpy.mgrid[
            offsets['yoff']:offsets['yoff']+offsets['win_ysize'],
            offsets['xoff']:offsets['xoff']+offsets['win_xsize']]
        grid_y = grid_y * geotransform[5] + geotransform[3]
        grid_x = grid_x * geotransform[1] + geotransform[0]

        # this is to be consistent with GDAL 2.0's change of 'nearest' to
        # 'near' for an interpolation scheme that SciPy did not change.
        if interpolation_mode == 'near':
            interpolation_mode = 'nearest'
        raster_out_array = scipy.interpolate.griddata(
            point_array, value_array, (grid_y, grid_x), interpolation_mode,
            nodata)
        band.WriteArray(raster_out_array, offsets['xoff'], offsets['yoff'])


def zonal_statistics(
        base_raster_path_band, aggregate_vector_path,
        aggregate_field_name, aggregate_layer_name=None,
        ignore_nodata=True, all_touched=False, polygons_might_overlap=True,
        working_dir=None):
    """Collect stats on pixel values which lie within polygons.

    This function summarizes raster statistics including min, max,
    mean, stddev, and pixel count over the regions on the raster that are
    overlaped by the polygons in the vector layer.  This function can
    handle cases where polygons overlap, which is notable since zonal stats
    functions provided by ArcGIS or QGIS usually incorrectly aggregate
    these areas.  Overlap avoidance is achieved by calculating a minimal set
    of disjoint non-overlapping polygons from `aggregate_vector_path` and
    rasterizing each set separately during the raster aggregation phase.  That
    set of rasters are then used to calculate the zonal stats of all polygons
    without aggregating vector overlap.

    Parameters:
        base_raster_path_band (tuple): a str/int tuple indicating the path to
            the base raster and the band index of that raster to analyze.
        aggregate_vector_path (string): a path to an ogr compatable polygon
            vector whose geometric features indicate the areas over
            `base_raster_path_band` to calculate statistics over.
        aggregate_field_name (string): field name in `aggregate_vector_path`
            that represents an identifying value for a given polygon. Result
            of this function will be indexed by the values found in this
            field.
        aggregate_layer_name (string): name of shapefile layer that will be
            used to aggregate results over.  If set to None, the first layer
            in the DataSource will be used as retrieved by `.GetLayer()`.
            Note: it is normal and expected to set this field at None if the
            aggregating shapefile is a single layer as many shapefiles,
            including the common 'ESRI Shapefile', are.
        ignore_nodata: if true, then nodata pixels are not accounted for when
            calculating min, max, count, or mean.  However, the value of
            `nodata_count` will always be the number of nodata pixels
            aggregated under the polygon.
        all_touched (boolean): if true will account for any pixel whose
            geometry passes through the pixel, not just the center point.
        polygons_might_overlap (boolean): if True the function calculates
            aggregation coverage close to optimally by rasterizing sets of
            polygons that don't overlap.  However, this step can be
            computationally expensive for cases where there are many polygons.
            Setting this flag to False directs the function rasterize in one
            step.
        working_dir (string): If not None, indicates where temporary files
            should be created during this run.

    Returns:
        nested dictionary indexed by aggregating feature id, and then by one
        of 'min' 'max' 'sum' 'mean' 'count' and 'nodata_count'.  Example:
        {0: {'min': 0, 'max': 1, 'mean': 0.5, 'count': 2, 'nodata_count': 1}}

    """
    if not _is_raster_path_band_formatted(base_raster_path_band):
        raise ValueError(
            "`base_raster_path_band` not formatted as expected.  Expects "
            "(path, band_index), recieved %s" + base_raster_path_band)
    aggregate_vector = gdal.OpenEx(aggregate_vector_path)
    if aggregate_layer_name is not None:
        aggregate_layer = aggregate_vector.GetLayerByName(
            aggregate_layer_name)
    else:
        aggregate_layer = aggregate_vector.GetLayer()
    aggregate_layer_defn = aggregate_layer.GetLayerDefn()
    aggregate_field_index = aggregate_layer_defn.GetFieldIndex(
        aggregate_field_name)
    if aggregate_field_index == -1:  # -1 returned when field does not exist.
        # Raise exception if user provided a field that's not in vector
        raise ValueError(
            'Vector %s must have a field named %s' %
            (aggregate_vector_path, aggregate_field_name))

    # create a new aggregate ID field to map base vector aggregate fields to
    # local ones that are guaranteed to be integers.
    local_aggregate_field_name = str(uuid.uuid4())[-8:-1]
    local_aggregate_field_def = ogr.FieldDefn(
        local_aggregate_field_name, ogr.OFTInteger)

    # Adding the rasterize by attribute option
    rasterize_layer_args = {
        'options': [
            'ALL_TOUCHED=%s' % str(all_touched).upper(),
            'ATTRIBUTE=%s' % local_aggregate_field_name]
        }

    # clip base raster to aggregating vector intersection
    raster_info = get_raster_info(base_raster_path_band[0])
    # -1 here because bands are 1 indexed
    raster_nodata = raster_info['nodata'][base_raster_path_band[1]-1]
    with tempfile.NamedTemporaryFile(
            prefix='clipped_raster', delete=False,
            dir=working_dir) as clipped_raster_file:
        clipped_raster_path = clipped_raster_file.name
    align_and_resize_raster_stack(
        [base_raster_path_band[0]], [clipped_raster_path], ['near'],
        raster_info['pixel_size'], 'intersection',
        base_vector_path_list=[aggregate_vector_path], raster_align_index=0)
    clipped_raster = gdal.OpenEx(clipped_raster_path)

    # make a shapefile that non-overlapping layers can be added to
    driver = ogr.GetDriverByName('ESRI Shapefile')
    disjoint_vector_dir = tempfile.mkdtemp(dir=working_dir)
    disjoint_vector = driver.CreateDataSource(
        os.path.join(disjoint_vector_dir, 'disjoint_vector.shp'))
    spat_ref = aggregate_layer.GetSpatialRef()

    # Initialize these dictionaries to have the shapefile fields in the
    # original datasource even if we don't pick up a value later
    base_to_local_aggregate_value = {}
    for feature in aggregate_layer:
        aggregate_field_value = feature.GetField(aggregate_field_name)
        # this builds up a map of aggregate field values to unique ids
        if aggregate_field_value not in base_to_local_aggregate_value:
            base_to_local_aggregate_value[aggregate_field_value] = len(
                base_to_local_aggregate_value)
    aggregate_layer.ResetReading()

    # Loop over each polygon and aggregate
    if polygons_might_overlap:
        minimal_polygon_sets = calculate_disjoint_polygon_set(
            aggregate_vector_path)
    else:
        minimal_polygon_sets = [
            set([feat.GetFID() for feat in aggregate_layer])]

    clipped_band = clipped_raster.GetRasterBand(base_raster_path_band[1])

    with tempfile.NamedTemporaryFile(
            prefix='aggregate_id_raster',
            delete=False, dir=working_dir) as aggregate_id_raster_file:
        aggregate_id_raster_path = aggregate_id_raster_file.name

    aggregate_id_nodata = len(base_to_local_aggregate_value)
    new_raster_from_base(
        clipped_raster_path, aggregate_id_raster_path, gdal.GDT_Int32,
        [aggregate_id_nodata])
    aggregate_id_raster = gdal.OpenEx(aggregate_id_raster_path, gdal.GA_Update)
    aggregate_stats = {}

    for polygon_set in minimal_polygon_sets:
        disjoint_layer = disjoint_vector.CreateLayer(
            'disjoint_vector', spat_ref, ogr.wkbPolygon)
        disjoint_layer.CreateField(local_aggregate_field_def)
        # add polygons to subset_layer
        for index, poly_fid in enumerate(polygon_set):
            poly_feat = aggregate_layer.GetFeature(poly_fid)
            disjoint_layer.CreateFeature(poly_feat)
            # we seem to need to reload the feature and set the index because
            # just copying over the feature left indexes as all 0s.  Not sure
            # why.
            new_feat = disjoint_layer.GetFeature(index)
            new_feat.SetField(
                local_aggregate_field_name, base_to_local_aggregate_value[
                    poly_feat.GetField(aggregate_field_name)])
            disjoint_layer.SetFeature(new_feat)
        disjoint_layer.SyncToDisk()

        # nodata out the mask
        aggregate_id_band = aggregate_id_raster.GetRasterBand(1)
        aggregate_id_band.Fill(aggregate_id_nodata)
        aggregate_id_band = None

        gdal.RasterizeLayer(
            aggregate_id_raster, [1], disjoint_layer, **rasterize_layer_args)
        aggregate_id_raster.FlushCache()

        # Delete the features we just added to the subset_layer
        disjoint_layer = None
        disjoint_vector.DeleteLayer(0)

        # create a key array
        # and parallel min, max, count, and nodata count arrays
        for aggregate_id_offsets, aggregate_id_block in iterblocks(
                aggregate_id_raster_path):
            clipped_block = clipped_band.ReadAsArray(**aggregate_id_offsets)
            # guard against a None nodata type
            valid_mask = numpy.ones(aggregate_id_block.shape, dtype=bool)
            if aggregate_id_nodata is not None:
                valid_mask[:] = aggregate_id_block != aggregate_id_nodata
            valid_aggregate_id = aggregate_id_block[valid_mask]
            valid_clipped = clipped_block[valid_mask]
            for aggregate_id in numpy.unique(valid_aggregate_id):
                aggregate_mask = valid_aggregate_id == aggregate_id
                masked_clipped_block = valid_clipped[aggregate_mask]
                clipped_nodata_mask = (masked_clipped_block == raster_nodata)
                if aggregate_id not in aggregate_stats:
                    aggregate_stats[aggregate_id] = {
                        'min': None,
                        'max': None,
                        'count': 0,
                        'nodata_count': 0,
                        'sum': 0.0
                    }
                aggregate_stats[aggregate_id]['nodata_count'] += (
                    numpy.count_nonzero(clipped_nodata_mask))
                if ignore_nodata:
                    masked_clipped_block = (
                        masked_clipped_block[~clipped_nodata_mask])
                if masked_clipped_block.size == 0:
                    continue

                if aggregate_stats[aggregate_id]['min'] is None:
                    aggregate_stats[aggregate_id]['min'] = (
                        masked_clipped_block[0])
                    aggregate_stats[aggregate_id]['max'] = (
                        masked_clipped_block[0])

                aggregate_stats[aggregate_id]['min'] = min(
                    numpy.min(masked_clipped_block),
                    aggregate_stats[aggregate_id]['min'])
                aggregate_stats[aggregate_id]['max'] = max(
                    numpy.max(masked_clipped_block),
                    aggregate_stats[aggregate_id]['max'])
                aggregate_stats[aggregate_id]['count'] += (
                    masked_clipped_block.size)
                aggregate_stats[aggregate_id]['sum'] += numpy.sum(
                    masked_clipped_block)

    # clean up temporary files
    clipped_band = None
    clipped_raster = None
    aggregate_id_raster = None
    disjoint_layer = None
    disjoint_vector = None
    for filename in [aggregate_id_raster_path, clipped_raster_path]:
        os.remove(filename)
    shutil.rmtree(disjoint_vector_dir)

    # map the local ids back to the original base value
    local_to_base_aggregate_value = {
        value: key for key, value in
        base_to_local_aggregate_value.iteritems()}

    return {
        local_to_base_aggregate_value[key]: value
        for key, value in aggregate_stats.iteritems()}


def get_vector_info(vector_path, layer_index=0):
    """Get information about an OGR vector (datasource).

    Parameters:
        vector_path (str): a path to a OGR vector.
        layer_index (int): index of underlying layer to analyze.  Defaults to
            0.

    Returns:
        raster_properties (dictionary): a dictionary with the following
            properties stored under relevant keys.

            'projection' (string): projection of the vector in Well Known
                Text.
            'bounding_box' (list): list of floats representing the bounding
                box in projected coordinates as [minx, miny, maxx, maxy].

    """
    vector = gdal.OpenEx(vector_path)
    vector_properties = {}
    layer = vector.GetLayer(iLayer=layer_index)
    # projection is same for all layers, so just use the first one
    vector_properties['projection'] = layer.GetSpatialRef().ExportToWkt()
    layer_bb = layer.GetExtent()
    layer = None
    vector = None
    # convert form [minx,maxx,miny,maxy] to [minx,miny,maxx,maxy]
    vector_properties['bounding_box'] = [layer_bb[i] for i in [0, 2, 1, 3]]
    return vector_properties


def get_raster_info(raster_path):
    """Get information about a GDAL raster (dataset).

    Parameters:
       raster_path (String): a path to a GDAL raster.

    Returns:
        raster_properties (dictionary): a dictionary with the properties
            stored under relevant keys.

            'pixel_size' (tuple): (pixel x-size, pixel y-size) from
                geotransform.
            'mean_pixel_size' (float): the average size of the absolute value
                of each pixel size element.
            'raster_size' (tuple):  number of raster pixels in (x, y)
                direction.
            'nodata' (list): a list of the nodata values in the bands of the
                raster in the same order as increasing band index.
            'n_bands' (int): number of bands in the raster.
            'geotransform' (tuple): a 6-tuple representing the geotransform of
                (x orign, x-increase, xy-increase,
                 y origin, yx-increase, y-increase).
            'datatype' (int): An instance of an enumerated gdal.GDT_* int
                that represents the datatype of the raster.
            'projection' (string): projection of the raster in Well Known
                Text.
            'bounding_box' (list): list of floats representing the bounding
                box in projected coordinates as [minx, miny, maxx, maxy]
            'block_size' (tuple): underlying x/y raster block size for
                efficient reading.

    """
    raster_properties = {}
    raster = gdal.OpenEx(raster_path)
    raster_properties['projection'] = raster.GetProjection()
    geo_transform = raster.GetGeoTransform()
    raster_properties['geotransform'] = geo_transform
    raster_properties['pixel_size'] = (geo_transform[1], geo_transform[5])
    raster_properties['mean_pixel_size'] = (
        (abs(geo_transform[1]) + abs(geo_transform[5])) / 2.0)
    raster_properties['raster_size'] = (
        raster.GetRasterBand(1).XSize,
        raster.GetRasterBand(1).YSize)
    raster_properties['n_bands'] = raster.RasterCount
    raster_properties['nodata'] = [
        raster.GetRasterBand(index).GetNoDataValue() for index in xrange(
            1, raster_properties['n_bands']+1)]
    # blocksize is the same for all bands, so we can just get the first
    raster_properties['block_size'] = raster.GetRasterBand(1).GetBlockSize()

    # we dont' really know how the geotransform is laid out, all we can do is
    # calculate the x and y bounds, then take the appropriate min/max
    x_bounds = [
        geo_transform[0], geo_transform[0] +
        raster_properties['raster_size'][0] * geo_transform[1] +
        raster_properties['raster_size'][1] * geo_transform[2]]
    y_bounds = [
        geo_transform[3], geo_transform[3] +
        raster_properties['raster_size'][0] * geo_transform[4] +
        raster_properties['raster_size'][1] * geo_transform[5]]

    raster_properties['bounding_box'] = [
        numpy.min(x_bounds), numpy.min(y_bounds),
        numpy.max(x_bounds), numpy.max(y_bounds)]

    # datatype is the same for the whole raster, but is associated with band
    raster_properties['datatype'] = raster.GetRasterBand(1).DataType
    raster = None
    return raster_properties


def reproject_vector(
        base_vector_path, target_wkt, target_path, layer_index=0,
        driver_name='ESRI Shapefile'):
    """Reproject OGR DataSource (vector).

    Transforms the features of the base vector to the desired output
    projection in a new ESRI Shapefile.

    Parameters:
        base_vector_path (string): Path to the base shapefile to transform.
        target_wkt (string): the desired output projection in Well Known Text
            (by layer.GetSpatialRef().ExportToWkt())
        target_path (string): the filepath to the transformed shapefile
        layer_index (int): index of layer in `base_vector_path` to reproject.
            Defaults to 0.
        driver_name (string): String to pass to ogr.GetDriverByName, defaults
            to 'ESRI Shapefile'.

    Returns:
        None

    """
    base_vector = gdal.OpenEx(base_vector_path)

    # if this file already exists, then remove it
    if os.path.isfile(target_path):
        LOGGER.warn(
            "%s already exists, removing and overwriting", target_path)
        os.remove(target_path)

    target_sr = osr.SpatialReference(target_wkt)

    # create a new shapefile from the orginal_datasource
    target_driver = ogr.GetDriverByName(driver_name)
    target_vector = target_driver.CreateDataSource(target_path)

    layer = base_vector.GetLayer(layer_index)
    layer_dfn = layer.GetLayerDefn()

    # Create new layer for target_vector using same name and
    # geometry type from base vector but new projection
    target_layer = target_vector.CreateLayer(
        layer_dfn.GetName(), target_sr, layer_dfn.GetGeomType())

    # Get the number of fields in original_layer
    original_field_count = layer_dfn.GetFieldCount()

    # For every field, create a duplicate field in the new layer
    for fld_index in xrange(original_field_count):
        original_field = layer_dfn.GetFieldDefn(fld_index)
        target_field = ogr.FieldDefn(
            original_field.GetName(), original_field.GetType())
        target_layer.CreateField(target_field)

    # Get the SR of the original_layer to use in transforming
    base_sr = layer.GetSpatialRef()

    # Create a coordinate transformation
    coord_trans = osr.CoordinateTransformation(base_sr, target_sr)

    # Copy all of the features in layer to the new shapefile
    error_count = 0
    for base_feature in layer:
        geom = base_feature.GetGeometryRef()
        if geom is None:
            # we encountered this error occasionally when transforming clipped
            # global polygons.  Not clear what is happening but perhaps a
            # feature was retained that otherwise wouldn't have been included
            # in the clip
            error_count += 1
            continue

        # Transform geometry into format desired for the new projection
        error_code = geom.Transform(coord_trans)
        if error_code != 0:  # error
            # this could be caused by an out of range transformation
            # whatever the case, don't put the transformed poly into the
            # output set
            error_count += 1
            continue

        # Copy original_datasource's feature and set as new shapes feature
        target_feature = ogr.Feature(target_layer.GetLayerDefn())
        target_feature.SetGeometry(geom)

        # For all the fields in the feature set the field values from the
        # source field
        for fld_index in xrange(target_feature.GetFieldCount()):
            target_feature.SetField(
                fld_index, base_feature.GetField(fld_index))

        target_layer.CreateFeature(target_feature)
        target_feature = None
        base_feature = None
    if error_count > 0:
        LOGGER.warn(
            '%d features out of %d were unable to be transformed and are'
            ' not in the output vector at %s', error_count,
            layer.GetFeatureCount(), target_path)
    layer = None
    base_vector = None


def reclassify_raster(
        base_raster_path_band, value_map, target_raster_path, target_datatype,
        target_nodata, values_required=True):
    """Reclassify pixel values in a raster.

    A function to reclassify values in raster to any output type. By default
    the values except for nodata must be in `value_map`.

    Parameters:
        base_raster_path_band (tuple): a tuple including file path to a raster
            and the band index to operate over. ex: (path, band_index)
        value_map (dictionary): a dictionary of values of
            {source_value: dest_value, ...} where source_value's type is the
            same as the values in `base_raster_path` at band `band_index`.
            Must contain at least one value.
        target_raster_path (string): target raster output path; overwritten if
            it exists
        target_datatype (gdal type): the numerical type for the target raster
        target_nodata (numerical type): the nodata value for the target raster
            Must be the same type as target_datatype
        band_index (int): Indicates which band in `base_raster_path` the
            reclassification should operate on.  Defaults to 1.
        values_required (bool): If True, raise a ValueError if there is a
            value in the raster that is not found in value_map.

    Returns:
        None

    Raises:
        ValueError if values_required is True and the value from
           'key_raster' is not a key in 'attr_dict'

    """
    if len(value_map) == 0:
        raise ValueError("value_map must contain at least one value")
    if not _is_raster_path_band_formatted(base_raster_path_band):
        raise ValueError(
            "Expected a (path, band_id) tuple, instead got '%s'" %
            base_raster_path_band)
    raster_info = get_raster_info(base_raster_path_band[0])
    nodata = raster_info['nodata'][base_raster_path_band[1]-1]
    value_map_copy = value_map.copy()
    # possible that nodata value is not defined, so test for None first
    # otherwise if nodata not predefined, remap it into the dictionary
    if nodata is not None and nodata not in value_map_copy:
        value_map_copy[nodata] = target_nodata
    keys = sorted(numpy.array(value_map_copy.keys()))
    values = numpy.array([value_map_copy[x] for x in keys])

    def _map_dataset_to_value_op(original_values):
        """Convert a block of original values to the lookup values."""
        if values_required:
            unique = numpy.unique(original_values)
            has_map = numpy.in1d(unique, keys)
            if not all(has_map):
                missing_values = unique[~has_map]
                raise ValueError(
                    'The following %d raster values %s from "%s" do not have '
                    'corresponding entries in the `value_map`: %s' % (
                        missing_values.size, str(missing_values),
                        base_raster_path_band[0], str(value_map)))
        index = numpy.digitize(original_values.ravel(), keys, right=True)
        return values[index].reshape(original_values.shape)

    raster_calculator(
        [base_raster_path_band], _map_dataset_to_value_op,
        target_raster_path, target_datatype, target_nodata)


def warp_raster(
        base_raster_path, target_pixel_size, target_raster_path,
        resample_method, target_bb=None, target_sr_wkt=None,
        gtiff_creation_options=_DEFAULT_GTIFF_CREATION_OPTIONS):
    """Resize/resample raster to desired pixel size, bbox and projection.

    Parameters:
        base_raster_path (string): path to base raster.
        target_pixel_size (list): a two element list or tuple indicating the
            x and y pixel size in projected units.
        target_raster_path (string): the location of the resized and
            resampled raster.
        resample_method (string): the resampling technique, one of
            "near|bilinear|cubic|cubic_spline|lanczos|average|mode|max"
            "min|med|q1|q3"
        target_bb (list): if None, target bounding box is the same as the
            source bounding box.  Otherwise it's a list of float describing
            target bounding box in target coordinate system as
            [minx, miny, maxx, maxy].
        target_sr_wkt (string): if not None, desired target projection in Well
            Known Text format.
        gtiff_creation_options (list or tuple): list of strings that will be
            passed as GDAL "dataset" creation options to the GTIFF driver.

    Returns:
        None

    """
    if target_bb is None:
        target_bb = get_raster_info(base_raster_path)['bounding_box']
        # transform the target_bb if target_sr_wkt is not None
        if target_sr_wkt is not None:
            target_bb = transform_bounding_box(
                get_raster_info(base_raster_path)['bounding_box'],
                get_raster_info(base_raster_path)['projection'],
                target_sr_wkt)

    target_x_size = int(
        abs((target_bb[2] - target_bb[0]) / target_pixel_size[0]))
    target_y_size = int(
        abs((target_bb[3] - target_bb[1]) / target_pixel_size[1]))

    if target_x_size == 0:
        LOGGER.warn(
            "bounding_box is so small that x dimension rounds to 0; "
            "clamping to 1.")
        LOGGER.debug(target_bb)
        target_bb[2] = target_bb[0] + abs(target_pixel_size[0])
        LOGGER.debug(target_bb)
        LOGGER.debug(target_pixel_size[0])
    if target_y_size == 0:
        LOGGER.warn(
            "bounding_box is so small that y dimension rounds to 0; "
            "clamping to 1.")
        LOGGER.debug(target_bb)
        target_bb[3] = target_bb[1] + abs(target_pixel_size[1])
        LOGGER.debug(target_bb)
        LOGGER.debug(target_pixel_size[1])

    reproject_callback = _make_logger_callback("Warp %.1f%% complete %s")

    base_raster = gdal.Open(base_raster_path)
    gdal.Warp(
        target_raster_path, base_raster,
        outputBounds=target_bb,
        xRes=abs(target_pixel_size[0]),
        yRes=abs(target_pixel_size[1]),
        resampleAlg=resample_method,
        outputBoundsSRS=target_sr_wkt,
        multithread=True,
        warpOptions=['NUM_THREADS=ALL_CPUS'],
        creationOptions=gtiff_creation_options,
        callback=reproject_callback,
        callback_data=[target_raster_path])


def rasterize(
        vector_path, target_raster_path, burn_values, option_list,
        layer_index=0):
    """Project a vector onto an existing raster.

    Burn the layer at `layer_index` in `vector_path` to an existing
    raster at `target_raster_path_band`.

    Parameters:
        vector_path (string): filepath to vector to rasterize.
        target_raster_path (string): path to an existing raster to burn vector
            into.  Can have multiple bands.
        burn_values (list): list of values to burn into each band of the
            raster.  If used, should have the same length as number of bands
            at the `target_raster_path` raster.  Can otherwise be None.
        option_list (list): a list of burn options (or None if not used), each
            element is a string of the form:
                "ATTRIBUTE=?": Identifies an attribute field on the features
                    to be used for a burn in value. The value will be burned
                    into all output bands. If specified, `burn_values`
                    will not be used and can be None.
                "CHUNKYSIZE=?": The height in lines of the chunk to operate
                    on. The larger the chunk size the less times we need to
                    make a pass through all the shapes. If it is not set or
                    set to zero the default chunk size will be used. Default
                    size will be estimated based on the GDAL cache buffer size
                    using formula: cache_size_bytes/scanline_size_bytes, so
                    the chunk will not exceed the cache.
                "ALL_TOUCHED=TRUE/FALSE": May be set to TRUE to set all pixels
                    touched by the line or polygons, not just those whose
                    center is within the polygon or that are selected by
                    Brezenhams line algorithm. Defaults to FALSE.
                "BURN_VALUE_FROM": May be set to "Z" to use the Z values of
                    the geometries. The value from burn_values or the
                    attribute field value is added to this before burning. In
                    default case dfBurnValue is burned as it is (richpsharp:
                    note, I'm not sure what this means, but copied from formal
                    docs). This is implemented properly only for points and
                    lines for now. Polygons will be burned using the Z value
                    from the first point.
                "MERGE_ALG=REPLACE/ADD": REPLACE results in overwriting of
                    value, while ADD adds the new value to the existing
                    raster, suitable for heatmaps for instance.

            Example: ["ATTRIBUTE=npv", "ALL_TOUCHED=TRUE"]

    Returns:
        None

    """
    gdal.PushErrorHandler('CPLQuietErrorHandler')
    raster = gdal.OpenEx(target_raster_path, gdal.GA_Update | gdal.OF_RASTER)
    gdal.PopErrorHandler()
    if raster is None:
        raise ValueError("%s doesn't exist, but needed to rasterize.")
    vector = gdal.OpenEx(vector_path)
    layer = vector.GetLayer(layer_index)

    rasterize_callback = _make_logger_callback(
        "RasterizeLayer %.1f%% complete %s")

    if burn_values is None:
        burn_values = []
    if option_list is None:
        option_list = []

    gdal.RasterizeLayer(
        raster, [1], layer, burn_values=burn_values, options=option_list,
        callback=rasterize_callback)
    raster.FlushCache()
    gdal.Dataset.__swig_destroy__(raster)


def calculate_disjoint_polygon_set(vector_path, layer_index=0):
    """Create a list of sets of polygons that don't overlap.

    Determining the minimal number of those sets is an np-complete problem so
    this is an approximation that builds up sets of maximal subsets.

    Parameters:
        vector_path (string): a path to an OGR vector.
        layer_index (int): index of underlying layer in `vector_path` to
            calculate disjoint set. Defaults to 0.

    Returns:
        subset_list (list): list of sets of FIDs from vector_path

    """
    vector = gdal.OpenEx(vector_path)
    vector_layer = vector.GetLayer(layer_index)

    poly_intersect_lookup = {}
    for poly_feat in vector_layer:
        poly_wkt = poly_feat.GetGeometryRef().ExportToWkt()
        shapely_polygon = shapely.wkt.loads(poly_wkt)
        poly_wkt = None
        poly_fid = poly_feat.GetFID()
        poly_intersect_lookup[poly_fid] = {
            'poly': shapely_polygon,
            'intersects': set(),
        }
    vector_layer = None
    vector = None

    for poly_fid in poly_intersect_lookup:
        polygon = shapely.prepared.prep(
            poly_intersect_lookup[poly_fid]['poly'])
        for intersect_poly_fid in poly_intersect_lookup:
            if intersect_poly_fid == poly_fid or polygon.intersects(
                    poly_intersect_lookup[intersect_poly_fid]['poly']):
                poly_intersect_lookup[poly_fid]['intersects'].add(
                    intersect_poly_fid)
        polygon = None

    # Build maximal subsets
    subset_list = []
    while len(poly_intersect_lookup) > 0:
        # sort polygons by increasing number of intersections
        heap = []
        for poly_fid, poly_dict in poly_intersect_lookup.iteritems():
            heapq.heappush(
                heap, (len(poly_dict['intersects']), poly_fid, poly_dict))

        # build maximal subset
        maximal_set = set()
        while len(heap) > 0:
            _, poly_fid, poly_dict = heapq.heappop(heap)
            for maxset_fid in maximal_set:
                if maxset_fid in poly_intersect_lookup[poly_fid]['intersects']:
                    # it intersects and can't be part of the maximal subset
                    break
            else:
                # made it through without an intersection, add poly_fid to
                # the maximal set
                maximal_set.add(poly_fid)
                # remove that polygon and update the intersections
                del poly_intersect_lookup[poly_fid]
        # remove all the polygons from intersections once they're computed
        for maxset_fid in maximal_set:
            for poly_dict in poly_intersect_lookup.itervalues():
                poly_dict['intersects'].discard(maxset_fid)
        subset_list.append(maximal_set)
    return subset_list


def distance_transform_edt(
        base_mask_raster_path_band, target_distance_raster_path,
        working_dir=None):
    """Calculate the euclidean distance transform on base raster.

    Calculates the euclidean distance transform on the base raster in units of
    pixels.

    Parameters:
        base_raster_path_band (tuple): a tuple including file path to a raster
            and the band index to operate over. eg: (path, band_index)
        target_distance_raster_path (string): will make a float raster w/ same
            dimensions and projection as base_mask_raster_path_band where all
            zero values of base_mask_raster_path_band are equal to the
            euclidean distance to the
            closest non-zero pixel.
         working_dir (string): If not None, indicates where temporary files
            should be created during this run.

    Returns:
        None

    """
    with tempfile.NamedTemporaryFile(
            prefix='dt_mask', delete=False, dir=working_dir) as dt_mask_file:
        dt_mask_path = dt_mask_file.name
    raster_info = get_raster_info(base_mask_raster_path_band[0])
    nodata = raster_info['nodata'][base_mask_raster_path_band[1]-1]
    nodata_out = 255

    def mask_op(base_array):
        """Convert base_array to 1 if >0, 0 if == 0 or nodata."""
        return numpy.where(
            base_array == nodata, nodata_out, base_array != 0)

    raster_calculator(
        [base_mask_raster_path_band], mask_op,
        dt_mask_path, gdal.GDT_Byte, nodata_out, calc_raster_stats=False)
    geoprocessing_core.distance_transform_edt(
        (dt_mask_path, 1), target_distance_raster_path)
    try:
        os.remove(dt_mask_path)
    except OSError:
        LOGGER.warn("couldn't remove file %s", dt_mask_path)


def _next_regular(base):
    """
    Find the next regular number greater than or equal to base.

    Regular numbers are composites of the prime factors 2, 3, and 5.
    Also known as 5-smooth numbers or Hamming numbers, these are the optimal
    size for inputs to FFTPACK.

    This source was taken directly from scipy.signaltools and saves us from
    having to access a protected member in a library that could change in
    future releases:

    https://github.com/scipy/scipy/blob/v0.17.1/scipy/signal/signaltools.py#L211

    Parameters:
        base (int): a positive integer to start to find the next Hamming
            number.

    Returns:
        The next regular number greater than or equal to `base`.
    """
    if base <= 6:
        return base

    # Quickly check if it's already a power of 2
    if not (base & (base-1)):
        return base

    match = float('inf')  # Anything found will be smaller
    p5 = 1
    while p5 < base:
        p35 = p5
        while p35 < base:
            # Ceiling integer division, avoiding conversion to float
            # (quotient = ceil(base / p35))
            quotient = -(-base // p35)

            # Quickly find next power of 2 >= quotient
            p2 = 2**((quotient - 1).bit_length())

            N = p2 * p35
            if N == base:
                return N
            elif N < match:
                match = N
            p35 *= 3
            if p35 == base:
                return p35
        if p35 < match:
            match = p35
        p5 *= 5
        if p5 == base:
            return p5
    if p5 < match:
        match = p5
    return match


def convolve_2d(
        signal_path_band, kernel_path_band, target_path,
        ignore_nodata=False, mask_nodata=True, normalize_kernel=False,
        target_datatype=gdal.GDT_Float64,
        target_nodata=None,
        gtiff_creation_options=_DEFAULT_GTIFF_CREATION_OPTIONS,
        working_dir=None):
    """Convolve 2D kernel over 2D signal.

    Convolves the raster in `kernel_path_band` over `signal_path_band`.
    Nodata values are treated as 0.0 during the convolution and masked to
    nodata for the output result where `signal_path` has nodata.

    Parameters:
        signal_path_band (tuple): a 2 tuple of the form
            (filepath to signal raster, band index).
        kernel_path_band (tuple): a 2 tuple of the form
            (filepath to kernel raster, band index).
        target_path (string): filepath to target raster that's the convolution
            of signal with kernel.  Output will be a single band raster of
            same size and projection as `signal_path_band`. Any nodata pixels
            that align with `signal_path_band` will be set to nodata.
        ignore_nodata (boolean): If true, any pixels that are equal to
            `signal_path_band`'s nodata value are not included when averaging
            the convolution filter.
        normalize_kernel (boolean): If true, the result is divided by the
            sum of the kernel.
        mask_nodata (boolean): If true, `target_path` raster's output is
            nodata where `signal_path_band`'s pixels were nodata.
        target_datatype (GDAL type): a GDAL raster type to set the output
            raster type to, as well as the type to calculate the convolution
            in.  Defaults to GDT_Float64.  Note unsigned byte is not
            supported.
        target_nodata (int/float): nodata value to set on output raster.
            If `target_datatype` is not gdal.GDT_Float64, this value must
            be set.  Otherwise defaults to the minimum value of a float32.
        gtiff_creation_options (list): an argument list that will be
            passed to the GTiff driver for creating `target_path`.  Useful for
            blocksizes, compression, and more.
         working_dir (string): If not None, indicates where temporary files
            should be created during this run.

    Returns:
        None

    """
    _gdal_type_to_numpy_lookup = {
        gdal.GDT_Byte: numpy.int8,
        gdal.GDT_Int16: numpy.int16,
        gdal.GDT_Int32: numpy.int32,
        gdal.GDT_UInt16: numpy.uint16,
        gdal.GDT_UInt32: numpy.uint32,
        gdal.GDT_Float32: numpy.float32,
        gdal.GDT_Float64: numpy.float64,
    }
    if target_datatype is not gdal.GDT_Float64 and target_nodata is None:
        raise ValueError(
            "`target_datatype` is set, but `target_nodata` is None. "
            "`target_nodata` must be set if `target_datatype` is not "
            "`gdal.GDT_Float64`.  `target_nodata` is set to None.")

    worker_pool = multiprocessing.pool.ThreadPool(4)

    multiprocessing_manager = multiprocessing.Manager()
    work_queue = multiprocessing_manager.Queue()
    write_queue = multiprocessing_manager.Queue()
    exception_queue = multiprocessing_manager.Queue()

    if target_nodata is None:
        target_nodata = numpy.finfo(numpy.float32).min
    LOGGER.debug("creating target raster %s", target_path)
    new_raster_from_base(
        signal_path_band[0], target_path, target_datatype, [target_nodata],
        gtiff_creation_options=gtiff_creation_options)
    LOGGER.debug("target raster created %s", target_path)

    signal_raster_info = get_raster_info(signal_path_band[0])

    # we need the original signal raster info because we want the output to
    # be clipped and NODATA masked to it
    signal_nodata = signal_raster_info['nodata']
    target_raster = gdal.OpenEx(target_path, gdal.GA_Update)
    target_band = target_raster.GetRasterBand(1)

    # if we're ignoring nodata, we need to make a parallel convolved signal
    # of the nodata mask
    mask_raster_path = None
    if signal_nodata is not None and ignore_nodata:
        mask_dir = tempfile.mkdtemp(dir=working_dir)
        mask_raster_path = os.path.join(mask_dir, 'convolved_mask.tif')
        mask_nodata = -1.0
        new_raster_from_base(
            signal_path_band[0], mask_raster_path, gdal.GDT_Float32,
            [mask_nodata], gtiff_creation_options=gtiff_creation_options)
        mask_raster = gdal.OpenEx(mask_raster_path, gdal.GA_Update)
        mask_band = mask_raster.GetRasterBand(1)

    LOGGER.info('starting convolve')
    last_time = time.time()

    convolve_2d_worker_result = threading.Thread(
        target=_convolve_2d_worker,
        args=(
            signal_path_band, kernel_path_band,
            ignore_nodata, normalize_kernel,
            work_queue, write_queue, exception_queue))
    convolve_2d_worker_result.start()

    convolve_2d_writer_result = threading.Thread(
        target=_convolve_2d_write_raster_worker,
        args=(
            write_queue, signal_path_band,
            mask_raster_path, target_path, ignore_nodata))
    convolve_2d_writer_result.start()

    n_pixels = target_raster.RasterXSize * target_raster.RasterYSize
    pixels_processed = 0
    for signal_offset in iterblocks(signal_path_band[0], offset_only=True):
        for kernel_offset in iterblocks(kernel_path_band[0], offset_only=True):
            work_queue.put((signal_offset, kernel_offset))

        pixels_processed += (
            signal_offset['win_xsize'] * signal_offset['win_ysize'])
        last_time = _invoke_timed_callback(
            last_time, lambda: LOGGER.info(
                "convolution %.2f%% complete",
                100.0 * float(pixels_processed) / n_pixels),
            _LOGGING_PERIOD)

    work_queue.put(None)
    LOGGER.debug("waiting for convolve 2d worker to terminate")
    convolve_2d_worker_result.join()
    LOGGER.debug("Convolve 2d worker terminated")

    LOGGER.debug("waiting for convolve 2d writer to terminate")
    convolve_2d_writer_result.join()
    LOGGER.debug("Convolve 2d writer terminated")

    if signal_nodata is not None and ignore_nodata:
        mask_band.FlushCache()
        mask_raster.FlushCache()
        for target_data, target_block in iterblocks(
                target_path, band_index_list=[1],
                astype_list=[_gdal_type_to_numpy_lookup[target_datatype]]):
            mask_block = mask_band.ReadAsArray(**target_data)
            if signal_nodata is not None and mask_nodata:
                valid_mask = target_block != target_nodata
            else:
                valid_mask = numpy.ones(target_block.shape, dtype=numpy.bool)
            # divide the target_band by the mask_band
            target_block[valid_mask] /= mask_block[valid_mask]

            # scale by kernel sum if necessary since mask division will
            # automatically normalize kernel
            if not normalize_kernel:
                target_block[valid_mask] *= kernel_sum

            target_band.WriteArray(
                target_block, xoff=target_data['xoff'],
                yoff=target_data['yoff'])
        # delete the mask raster
        gdal.Dataset.__swig_destroy__(mask_raster)
        os.remove(mask_raster_path)


def iterblocks(
        raster_path, band_index_list=None, largest_block=_LARGEST_ITERBLOCK,
        astype_list=None, offset_only=False):
    """Iterate across all the memory blocks in the input raster.

    Result is a generator of block location information and numpy arrays.

    This is especially useful when a single value needs to be derived from the
    pixel values in a raster, such as the sum total of all pixel values, or
    a sequence of unique raster values.  In such cases, `raster_local_op`
    is overkill, since it writes out a raster.

    As a generator, this can be combined multiple times with itertools.izip()
    to iterate 'simultaneously' over multiple rasters, though the user should
    be careful to do so only with prealigned rasters.

    Parameters:
        raster_path (string): Path to raster file to iterate over.
        band_index_list (list of ints or None): A list of band indexes for
            which the data blocks should be returned; band indexes start at 1.
            Defaults to None, which will return all bands.  Band indexes may
            be specified in any order, and band indexes may be specified
            multiple times.  The blocks returned on each iteration will be in
            the order specified in this list.
        largest_block (int): Attempts to iterate over raster blocks with
            this many elements.  Useful in cases where the blocksize is
            relatively small, memory is available, and the function call
            overhead dominates the iteration.  Defaults to 2**20.  A value of
            anything less than the original blocksize of the raster will
            result in blocksizes equal to the original size.
        astype_list (list of numpy types): If none, output blocks are in the
            native type of the raster bands.  Otherwise this parameter is a
            list of len(band_index_list) length that contains the desired
            output types that iterblock generates for each band.
        offset_only (boolean): defaults to False, if True `iterblocks` only
            returns offset dictionary and doesn't read any binary data from
            the raster.  This can be useful when iterating over writing to
            an output.

    Returns:
        If `offset_only` is false, on each iteration, a tuple containing a dict
        of block data and `n` 2-dimensional numpy arrays are returned, where
        `n` is the number of bands requested via `band_list`. The dict of
        block data has these attributes:

            data['xoff'] - The X offset of the upper-left-hand corner of the
                block.
            data['yoff'] - The Y offset of the upper-left-hand corner of the
                block.
            data['win_xsize'] - The width of the block.
            data['win_ysize'] - The height of the block.

        If `offset_only` is True, the function returns only the block offset
            data and does not attempt to read binary data from the raster.

    """
    raster = gdal.OpenEx(raster_path)

    if band_index_list is None:
        band_index_list = range(1, raster.RasterCount + 1)

    band_index_list = [
        raster.GetRasterBand(index) for index in band_index_list]

    block = band_index_list[0].GetBlockSize()
    cols_per_block = block[0]
    rows_per_block = block[1]

    n_cols = raster.RasterXSize
    n_rows = raster.RasterYSize

    block_area = cols_per_block * rows_per_block
    # try to make block wider
    if largest_block / block_area > 0:
        width_factor = largest_block / block_area
        cols_per_block *= width_factor
        if cols_per_block > n_cols:
            cols_per_block = n_cols
        block_area = cols_per_block * rows_per_block
    # try to make block taller
    if largest_block / block_area > 0:
        height_factor = largest_block / block_area
        rows_per_block *= height_factor
        if rows_per_block > n_rows:
            rows_per_block = n_rows

    n_col_blocks = int(math.ceil(n_cols / float(cols_per_block)))
    n_row_blocks = int(math.ceil(n_rows / float(rows_per_block)))

    # Initialize to None so a block array is created on the first iteration
    last_row_block_width = None
    last_col_block_width = None

    if astype_list is not None:
        block_type_list = astype_list
    else:
        block_type_list = [
            _gdal_to_numpy_type(ds_band) for ds_band in band_index_list]

    for row_block_index in xrange(n_row_blocks):
        row_offset = row_block_index * rows_per_block
        row_block_width = n_rows - row_offset
        if row_block_width > rows_per_block:
            row_block_width = rows_per_block

        for col_block_index in xrange(n_col_blocks):
            col_offset = col_block_index * cols_per_block
            col_block_width = n_cols - col_offset
            if col_block_width > cols_per_block:
                col_block_width = cols_per_block

            # resize the raster block cache if necessary
            if (last_row_block_width != row_block_width or
                    last_col_block_width != col_block_width):
                raster_blocks = [
                    numpy.zeros(
                        (row_block_width, col_block_width),
                        dtype=block_type) for block_type in
                    block_type_list]

            offset_dict = {
                'xoff': col_offset,
                'yoff': row_offset,
                'win_xsize': col_block_width,
                'win_ysize': row_block_width,
            }
            result = offset_dict
            if not offset_only:
                for ds_band, block in zip(band_index_list, raster_blocks):
                    ds_band.ReadAsArray(buf_obj=block, **offset_dict)
                result = (result,) + tuple(raster_blocks)
            yield result


def transform_bounding_box(
        bounding_box, base_ref_wkt, target_ref_wkt, edge_samples=11):
    """Transform input bounding box to output projection.

    This transform accounts for the fact that the reprojected square bounding
    box might be warped in the new coordinate system.  To account for this,
    the function samples points along the original bounding box edges and
    attempts to make the largest bounding box around any transformed point
    on the edge whether corners or warped edges.

    Parameters:
        bounding_box (list): a list of 4 coordinates in `base_epsg` coordinate
            system describing the bound in the order [xmin, ymin, xmax, ymax]
        base_ref_wkt (string): the spatial reference of the input coordinate
            system in Well Known Text.
        target_ref_wkt (string): the spatial reference of the desired output
            coordinate system in Well Known Text.
        edge_samples (int): the number of interpolated points along each
            bounding box edge to sample along. A value of 2 will sample just
            the corners while a value of 3 will also sample the corners and
            the midpoint.

    Returns:
        A list of the form [xmin, ymin, xmax, ymax] that describes the largest
        fitting bounding box around the original warped bounding box in
        `new_epsg` coordinate system.

    """
    base_ref = osr.SpatialReference()
    base_ref.ImportFromWkt(base_ref_wkt)

    target_ref = osr.SpatialReference()
    target_ref.ImportFromWkt(target_ref_wkt)

    transformer = osr.CoordinateTransformation(base_ref, target_ref)

    def _transform_point(point):
        """Transform an (x,y) point tuple from base_ref to target_ref."""
        trans_x, trans_y, _ = (transformer.TransformPoint(*point))
        return (trans_x, trans_y)

    # The following list comprehension iterates over each edge of the bounding
    # box, divides each edge into `edge_samples` number of points, then
    # reduces that list to an appropriate `bounding_fn` given the edge.
    # For example the left edge needs to be the minimum x coordinate so
    # we generate `edge_samples` number of points between the upper left and
    # lower left point, transform them all to the new coordinate system
    # then get the minimum x coordinate "min(p[0] ...)" of the batch.
    # points are numbered from 0 starting upper right as follows:
    # 0--3
    # |  |
    # 1--2
    p_0 = numpy.array((bounding_box[0], bounding_box[3]))
    p_1 = numpy.array((bounding_box[0], bounding_box[1]))
    p_2 = numpy.array((bounding_box[2], bounding_box[1]))
    p_3 = numpy.array((bounding_box[2], bounding_box[3]))
    transformed_bounding_box = [
        bounding_fn(
            [_transform_point(
                p_a * v + p_b * (1 - v)) for v in numpy.linspace(
                    0, 1, edge_samples)])
        for p_a, p_b, bounding_fn in [
            (p_0, p_1, lambda p_list: min([p[0] for p in p_list])),
            (p_1, p_2, lambda p_list: min([p[1] for p in p_list])),
            (p_2, p_3, lambda p_list: max([p[0] for p in p_list])),
            (p_3, p_0, lambda p_list: max([p[1] for p in p_list]))]]
    return transformed_bounding_box


def merge_rasters(
        raster_path_list, target_path, bounding_box=None,
        expected_nodata=None,
        gtiff_creation_options=_DEFAULT_GTIFF_CREATION_OPTIONS):
    """Merge the given rasters into a single raster.

    This operation creates a mosaic of the rasters in `raster_path_list`.
    The result is a raster of the size of the union of the bounding box of
    the inputs where the contents of each raster's bands are copied into the
    correct georeferenced target's bands.

    Note the input rasters must be in the same projection, same pixel size,
    same number of bands, and same datatype. If any of these are not true,
    the operation raises a ValueError with an appropriate error message.

    Parameters:
        raster_path_list (list): list of file paths to rasters
        target_path (string): path to the geotiff file that will be created
            by this operation.
        bounding_box (list): if not None, clip target path to be within these
            bounds.
        expected_nodata (float): if not None, use this as the nodata value
            in case multiple rasters have different nodata values.
        gtiff_creation_options (list): this is an argument list that will be
            passed to the GTiff driver.  Useful for blocksizes, compression,
            and more.

    Returns:
        None.

    """
    raster_info_list = [
        get_raster_info(path) for path in raster_path_list]
    pixel_size_set = set([
        x['pixel_size'] for x in raster_info_list])
    if len(pixel_size_set) != 1:
        raise ValueError(
            "Pixel sizes of all rasters are not the same. "
            "Here's the sizes: %s" % str([
                (path, x['pixel_size']) for path, x in zip(
                    raster_path_list, raster_info_list)]))
    n_bands_set = set([x['n_bands'] for x in raster_info_list])
    if len(n_bands_set) != 1:
        raise ValueError(
            "Number of bands per raster are not the same. "
            "Here's the band counts: %s" % str([
                (path, x['n_bands']) for path, x in zip(
                    raster_path_list, raster_info_list)]))

    datatype_set = set([x['datatype'] for x in raster_info_list])
    if len(datatype_set) != 1:
        raise ValueError(
            "Datatype per raster are not the same. "
            "Here's the datatypes: %s" % str([
                (path, x['datatype']) for path, x in zip(
                    raster_path_list, raster_info_list)]))

    if expected_nodata is None:
        nodata_set = set([x['nodata'][0] for x in raster_info_list])
        if len(nodata_set) != 1:
            raise ValueError(
                "Nodata per raster are not the same. "
                "Path and nodata values: %s" % str([
                    (path, x['nodata']) for path, x in zip(
                        raster_path_list, raster_info_list)]))

    projection_set = set([x['projection'] for x in raster_info_list])
    if len(projection_set) != 1:
        raise ValueError(
            "Projections are not identical. Here's the projections: %s" % str(
                [(path, x['projection']) for path, x in zip(
                    raster_path_list, raster_info_list)]))

    pixeltype_set = set()
    for path in raster_path_list:
        raster = gdal.Open(path)
        band = raster.GetRasterBand(1)
        metadata = band.GetMetadata('IMAGE_STRUCTURE')
        band = None
        if 'PIXELTYPE' in metadata:
            pixeltype_set.add('PIXELTYPE=' + metadata['PIXELTYPE'])
        else:
            pixeltype_set.add(None)
    if len(pixeltype_set) != 1:
        raise ValueError(
            "PIXELTYPE different between rasters."
            "Here is the set of types (should only have 1): %s" % str(
                pixeltype_set))

    bounding_box_list = [x['bounding_box'] for x in raster_info_list]
    target_bounding_box = reduce(
        functools.partial(_merge_bounding_boxes, mode='union'),
        bounding_box_list)
    if bounding_box is not None:
        target_bounding_box = reduce(
            functools.partial(_merge_bounding_boxes, mode='intersection'),
            [target_bounding_box, bounding_box])

    driver = gdal.GetDriverByName('GTiff')
    target_pixel_size = pixel_size_set.pop()
    n_cols = int(math.ceil(abs(
        (target_bounding_box[2]-target_bounding_box[0]) / target_pixel_size[0])))
    n_rows = int(math.ceil(abs(
        (target_bounding_box[3]-target_bounding_box[1]) / target_pixel_size[1])))

    target_geotransform = [
        target_bounding_box[0],
        target_pixel_size[0],
        0,
        target_bounding_box[1],
        0,
        target_pixel_size[1]]

    # sometimes we have negative pixel sizes so geotransform starts from the
    # other side
    if target_pixel_size[0] < 0:
        target_geotransform[0] = target_bounding_box[2]
    if target_pixel_size[1] < 0:
        target_geotransform[3] = target_bounding_box[3]

    # there's only one element in the sets so okay to pop right in the call,
    # we won't need it after anyway
    n_bands = n_bands_set.pop()
    target_raster = driver.Create(
        target_path.encode('utf-8'), n_cols, n_rows, n_bands,
        datatype_set.pop(), options=gtiff_creation_options)
    target_raster.SetProjection(raster.GetProjection())
    target_raster.SetGeoTransform(target_geotransform)
    if expected_nodata is None:
        nodata = nodata_set.pop()
    else:
        nodata = expected_nodata
    # consider what to do if rasters have nodata defined, but do not fill
    # up the mosaic.
    if nodata is not None:
        # geotiffs only have 1 nodata value set through the band
        target_raster.GetRasterBand(1).SetNoDataValue(nodata)
        for band_index in xrange(n_bands):
            target_raster.GetRasterBand(band_index+1).Fill(nodata)
    target_band_list = [
        target_raster.GetRasterBand(band_index) for band_index in xrange(
            1, n_bands+1)]

    # the raster was left over from checking pixel types, remove it after
    raster = None

    for raster_info, raster_path in zip(raster_info_list, raster_path_list):
        # figure out where raster_path starts w/r/t target_raster
        raster_start_x = int((
            raster_info['geotransform'][0] -
            target_geotransform[0]) / target_pixel_size[0])
        raster_start_y = int((
            raster_info['geotransform'][3] -
            target_geotransform[3]) / target_pixel_size[1])
        for iter_result in iterblocks(raster_path):
            offset_info = iter_result[0]
            # its possible the block reads in coverage that is outside the
            # target bounds entirely. nothing to do but skip
            if offset_info['yoff'] + raster_start_y > n_rows:
                continue
            if offset_info['xoff'] + raster_start_x > n_cols:
                continue
            if (offset_info['xoff'] + raster_start_x +
                    offset_info['win_xsize'] < 0):
                continue
            if (offset_info['yoff'] + raster_start_y +
                    offset_info['win_ysize'] < 0):
                continue

            # invariant: the window described in `offset_info` intersects
            # with the target raster.

            # check to see if window hangs off the left/top part of raster
            # and determine how far to adjust down
            x_clip_min = 0
            if raster_start_x + offset_info['xoff'] < 0:
                x_clip_min = abs(raster_start_x + offset_info['xoff'])
            y_clip_min = 0
            if raster_start_y + offset_info['yoff'] < 0:
                y_clip_min = abs(raster_start_y + offset_info['yoff'])
            x_clip_max = 0

            # check if window hangs off right/bottom part of target raster
            if (offset_info['xoff'] + raster_start_x +
                    offset_info['win_xsize'] >= n_cols):
                x_clip_max = (
                    offset_info['xoff'] + raster_start_x +
                    offset_info['win_xsize'] - n_cols)
            y_clip_max = 0

            if (offset_info['yoff'] + raster_start_y +
                    offset_info['win_ysize'] >= n_rows):
                y_clip_max = (
                    offset_info['yoff'] + raster_start_y +
                    offset_info['win_ysize'] - n_rows)

            data_block_list = iter_result[1:]
            for target_band, data_block in zip(
                    target_band_list, data_block_list):
                target_band.WriteArray(
                    data_block[
                        y_clip_min:offset_info['win_ysize']-y_clip_max,
                        x_clip_min:offset_info['win_xsize']-x_clip_max],
                    xoff=offset_info['xoff']+raster_start_x+x_clip_min,
                    yoff=offset_info['yoff']+raster_start_y+y_clip_min)

    del target_band_list[:]
    target_raster = None


def _invoke_timed_callback(
        reference_time, callback_lambda, callback_period):
    """Invoke callback if a certain amount of time has passed.

    This is a convenience function to standardize update callbacks from the
    module.

    Parameters:
        reference_time (float): time to base `callback_period` length from.
        callback_lambda (lambda): function to invoke if difference between
            current time and `reference_time` has exceeded `callback_period`.
        callback_period (float): time in seconds to pass until
            `callback_lambda` is invoked.

    Returns:
        `reference_time` if `callback_lambda` not invoked, otherwise the time
        when `callback_lambda` was invoked.

    """
    current_time = time.time()
    if current_time - reference_time > callback_period:
        callback_lambda()
        return current_time
    return reference_time


def _gdal_to_numpy_type(band):
    """Calculate the equivalent numpy datatype from a GDAL raster band type.

    This function doesn't handle complex or unknown types.  If they are
    passed in, this function will raise a ValueError.

    Parameters:
        band (gdal.Band): GDAL Band

    Returns:
        numpy_datatype (numpy.dtype): equivalent of band.DataType

    """
    # doesn't include GDT_Byte because that's a special case
    base_gdal_type_to_numpy = {
        gdal.GDT_Int16: numpy.int16,
        gdal.GDT_Int32: numpy.int32,
        gdal.GDT_UInt16: numpy.uint16,
        gdal.GDT_UInt32: numpy.uint32,
        gdal.GDT_Float32: numpy.float32,
        gdal.GDT_Float64: numpy.float64,
    }

    if band.DataType in base_gdal_type_to_numpy:
        return base_gdal_type_to_numpy[band.DataType]

    if band.DataType != gdal.GDT_Byte:
        raise ValueError("Unsupported DataType: %s" % str(band.DataType))

    # band must be GDT_Byte type, check if it is signed/unsigned
    metadata = band.GetMetadata('IMAGE_STRUCTURE')
    if 'PIXELTYPE' in metadata and metadata['PIXELTYPE'] == 'SIGNEDBYTE':
        return numpy.int8
    return numpy.uint8


def _merge_bounding_boxes(bb1, bb2, mode):
    """Merge two bounding boxes through union or intersection.

    Parameters:
        bb1, bb2 (list): list of float representing bounding box in the
            form bb=[minx,miny,maxx,maxy]
        mode (string); one of 'union' or 'intersection'

    Returns:
        Reduced bounding box of bb1/bb2 depending on mode.

    """
    def _less_than_or_equal(x_val, y_val):
        return x_val if x_val <= y_val else y_val

    def _greater_than(x_val, y_val):
        return x_val if x_val > y_val else y_val

    if mode == "union":
        comparison_ops = [
            _less_than_or_equal, _less_than_or_equal,
            _greater_than, _greater_than]
    if mode == "intersection":
        comparison_ops = [
            _greater_than, _greater_than,
            _less_than_or_equal, _less_than_or_equal]

    bb_out = [op(x, y) for op, x, y in zip(comparison_ops, bb1, bb2)]
    return bb_out


def _make_logger_callback(message):
    """Build a timed logger callback that prints `message` replaced.

    Parameters:
        message (string): a string that expects 2 placement %% variables,
            first for % complete from `df_complete`, second from
            `p_progress_arg[0]`.

    Returns:
        Function with signature:
            logger_callback(df_complete, psz_message, p_progress_arg)

    """
    def logger_callback(df_complete, _, p_progress_arg):
        """Argument names come from the GDAL API for callbacks."""
        try:
            current_time = time.time()
            if ((current_time - logger_callback.last_time) > 5.0 or
                    (df_complete == 1.0 and
                     logger_callback.total_time >= 5.0)):
                LOGGER.info(message, df_complete * 100, p_progress_arg[0])
                logger_callback.last_time = current_time
                logger_callback.total_time += current_time
        except AttributeError:
            logger_callback.last_time = time.time()
            logger_callback.total_time = 0.0

    return logger_callback


def _is_raster_path_band_formatted(raster_path_band):
    """Return true if raster path band is a (str, int) tuple/list."""
    if not isinstance(raster_path_band, (list, tuple)):
        return False
    elif len(raster_path_band) != 2:
        return False
    elif not isinstance(raster_path_band[0], types.StringTypes):
        return False
    elif not isinstance(raster_path_band[1], int):
        return False
    else:
        return True


def _write_block_worker(
        write_work_queue, target_raster_path, exception_queue):
    """Write (block_offset, numpy.array) tuples to the target raster.

    This function is used in a concurrency framework to write computed
    raster blocks to a target raster.

    Parameters:
        write_work_queue (Queue.Queue):  Contains (block_offset,
            numpy.array) tuples to write to the raster at
            `target_raster_path`.
        target_raster_path (string): path to an existing raster that will
            be written to during this call.
        exception_queue (Queue.Queue): if this function encounters an
            exception, it is pushed to this queue before returning.

    Returns:
        None.

    """
    LOGGER.debug('starting')
    try:
        target_raster = gdal.OpenEx(
            target_raster_path, gdal.GA_Update | gdal.OF_RASTER)
        if not target_raster:
            raise ValueError("Couldn't open raster %s" % target_raster_path)
        target_band = target_raster.GetRasterBand(1)

        while True:
            payload = write_work_queue.get()
            if payload is None:
                LOGGER.info(
                    "Empty payload, terminating. (%s)",
                    multiprocessing.current_process().name)
                break
            block_offset, block = payload
            target_band.WriteArray(
                block, xoff=block_offset['xoff'],
                yoff=block_offset['yoff'])
    except Exception as e:
        LOGGER.exception(
            "Exception occurred in _write_block_worker, pushing to exception "
            "queue and terminating")
        exception_queue.put(e)
        # drain the `write_work_queue` so another process isn't blocked on it
        while not write_work_queue.empty():
            write_work_queue.get()
    finally:
        target_band.FlushCache()
        target_band = None
        gdal.Dataset.__swig_destroy__(target_raster)
        LOGGER.debug('stopping')


def _local_op_worker(
        local_op, nodata_target, local_op_work_queue, write_block_queue,
        stats_worker_queue, exception_queue):
    """Threads `local_op` function for `raster_calculator`.

    Separating this function into a call that could be threaded is
    useful when local op uses a complex numpy calculation or a Cython
    function with the GIL released.

    As blocks are processed this function passes completed blocks to a
    write worker and a raster stats calculator if needed.

    Parameters:
        local_op (function): function that takes local_op(*payload) from
            `local_op_work_queue`.
        nodata_target (numeric): a numerical nodata value or None for
            determining statistics calculations.
        local_op_work_queue (Queue.Queue): a queue of
            (block_offset, raster_blocks) tuples such that local_op
            operates on local_op(*raster_blocks) and the result is written
            to the target raster at `block_offset`.
        write_block_queue (Queue.Queue): queue to put result of local_op
            effectively puting tuples
            (block_offset, local_op(*raster_blocks)).
        stats_worker_queue (Queue.Queue): if not None, the result of
            `local_op` that is not `nodata_target` will be put to this
            queue for stats processing.
        exception_queue (Queue.Queue): if this function encounters an
            exception, it is pushed to this queue before returning.

    Returns:
        None.

    """
    LOGGER.debug('starting')
    try:
        while True:
            payload = local_op_work_queue.get()
            if payload is None:
                break
            block_offset, raster_blocks = payload
            target_block = local_op(*raster_blocks)

            # send result to writer
            write_block_queue.put(
                (block_offset, target_block))

            # send result to stats calculator
            if stats_worker_queue:
                # guard against an undefined nodata target
                if nodata_target is not None:
                    valid_block = target_block[target_block != nodata_target]
                    if valid_block.size > 0:
                        stats_worker_queue.put(
                            valid_block)
                else:
                    stats_worker_queue.put(target_block)
    except Exception as e:
        LOGGER.exception(
            "Exception occurred in _local_op_worker, pushing to exception "
            "queue and terminating")
        exception_queue.put(e)
        # drain the work queue so that a thread waiting on the queue
        # doesn't block.
        while not local_op_work_queue.empty():
            local_op_work_queue.get()
    finally:
        LOGGER.debug('stopping, sending None to write and stats worker')
        write_block_queue.put(None, True, _MAX_TIMEOUT)
        if stats_worker_queue:
            stats_worker_queue.put(None, True, _MAX_TIMEOUT)


def _make_fft_cache():
    """Create a helper function to remember the last computed fft."""
    def _fft_cache(fshape, xoff, yoff, data_block):
        """Remember the last computed fft.

        Parameters:
            fshape (numpy.ndarray): shape of fft
            xoff,yoff (int): offsets of the data block
            data_block (numpy.ndarray): the 2D array to calculate the FFT
                on if not already calculated.

        Returns:
            fft transformed data_block of fshape size.

        """
        cache_key = (fshape[0], fshape[1], xoff, yoff)
        if cache_key != _fft_cache.key:
            _fft_cache.cache = numpy.fft.rfftn(data_block, fshape)
            _fft_cache.key = cache_key
        return _fft_cache.cache

    _fft_cache.cache = None
    _fft_cache.key = None
    return _fft_cache

def _convolve_2d_worker(
        signal_path_band, kernel_path_band,
        ignore_nodata, normalize_kernel,
        work_queue, write_queue, exception_queue):

    print 'starting convolve 2d worker'
    signal_raster = gdal.Open(signal_path_band[0])
    kernel_raster = gdal.Open(kernel_path_band[0])
    signal_band = signal_raster.GetRasterBand(signal_path_band[1])
    kernel_band = kernel_raster.GetRasterBand(kernel_path_band[1])

    signal_raster_info = get_raster_info(signal_path_band[0])
    kernel_raster_info = get_raster_info(kernel_path_band[0])

    n_cols_signal, n_rows_signal = signal_raster_info['raster_size']
    n_cols_kernel, n_rows_kernel = kernel_raster_info['raster_size']
    signal_nodata = signal_raster_info['nodata'][0]
    kernel_nodata = kernel_raster_info['nodata'][0]

    mask_result = None  # in case no mask is needed, variable is still defined

    _signal_fft_cache = _make_fft_cache()
    _kernel_fft_cache = _make_fft_cache()
    _mask_fft_cache = _make_fft_cache()

    # calculate the kernel sum for normalization
    kernel_sum = 0.0
    for _, kernel_block in iterblocks(kernel_path_band[0]):
        if kernel_nodata is not None and ignore_nodata:
            kernel_block[kernel_block == kernel_nodata] = 0.0
        kernel_sum += numpy.sum(kernel_block)

    while True:
        payload = work_queue.get()
        if payload is None:
            break

        signal_offset, kernel_offset = payload

        signal_block = signal_band.ReadAsArray(**signal_offset)
        kernel_block = kernel_band.ReadAsArray(**kernel_offset)

        if signal_nodata is not None and ignore_nodata:
            # if we're ignoring nodata, we don't want to add it up in the
            # convolution, so we zero those values out
            signal_nodata_mask = signal_block == signal_nodata
            signal_block[signal_nodata_mask] = 0.0

        left_index_raster = (
            signal_offset['xoff'] - n_cols_kernel / 2 + kernel_offset['xoff'])
        right_index_raster = (
            signal_offset['xoff'] - n_cols_kernel / 2 +
            kernel_offset['xoff'] + signal_offset['win_xsize'] +
            kernel_offset['win_xsize'] - 1)
        top_index_raster = (
            signal_offset['yoff'] - n_rows_kernel / 2 + kernel_offset['yoff'])
        bottom_index_raster = (
            signal_offset['yoff'] - n_rows_kernel / 2 +
            kernel_offset['yoff'] + signal_offset['win_ysize'] +
            kernel_offset['win_ysize'] - 1)

        # it's possible that the piece of the integrating kernel
        # doesn't affect the final result, if so we should skip
        if (right_index_raster < 0 or
                bottom_index_raster < 0 or
                left_index_raster > n_cols_signal or
                top_index_raster > n_rows_signal):
            continue

        if kernel_nodata is not None and ignore_nodata:
            kernel_block[kernel_block == kernel_nodata] = 0.0

        if normalize_kernel:
            kernel_block /= kernel_sum

        # determine the output convolve shape
        shape = (
            numpy.array(signal_block.shape) +
            numpy.array(kernel_block.shape) - 1)

        # add zero padding so FFT is fast
        fshape = [_next_regular(int(d)) for d in shape]

        signal_fft = _signal_fft_cache(
            fshape, signal_offset['xoff'], signal_offset['yoff'],
            signal_block)
        kernel_fft = _kernel_fft_cache(
            fshape, kernel_offset['xoff'], kernel_offset['yoff'],
            kernel_block)

        # this variable determines the output slice that doesn't include
        # the padded array region made for fast FFTs.
        fslice = tuple([slice(0, int(sz)) for sz in shape])
        # classic FFT convolution
        result = numpy.fft.irfftn(signal_fft * kernel_fft, fshape)[fslice]

        # if we're ignoring nodata, we need to make a convolution of the
        # nodata mask too
        if signal_nodata is not None and ignore_nodata:
            mask_fft = _mask_fft_cache(
                fshape, signal_offset['xoff'], signal_offset['yoff'],
                numpy.where(signal_nodata_mask, 0.0, 1.0))
            mask_result = numpy.fft.irfftn(
                mask_fft * kernel_fft, fshape)[fslice]

        # Add result to current output to account for overlapping edges
        index_tuple = (
            left_index_raster,
            top_index_raster,
            right_index_raster-left_index_raster,
            bottom_index_raster-top_index_raster
        )
        index_dict = {
            'xoff': index_tuple[0],
            'yoff': index_tuple[1],
            'win_xsize': index_tuple[2],
            'win_ysize': index_tuple[3]
        }

        print 'worker is putting this result to the write queue: ', result

        write_queue.put(
            (index_dict, result, mask_result, left_index_raster,
             right_index_raster, top_index_raster, bottom_index_raster))

    write_queue.put(None)


def _convolve_2d_write_raster_worker(
        write_raster_queue, signal_path_band,
        mask_raster_path, target_raster_path, ignore_nodata):
    target_processed_set = set()
    mask_processed_set = set()

    print 'write worker getting info',
    print signal_path_band
    print mask_raster_path
    print target_raster_path
    target_raster_info = get_raster_info(target_raster_path)
    signal_raster_info = get_raster_info(signal_path_band[0])
    target_nodata = target_raster_info['nodata'][0]

    n_cols_signal, n_rows_signal = signal_raster_info['raster_size']
    signal_nodata = signal_raster_info['nodata'][0]

    print 'write worker opening target, %s' % target_raster_path
    target_raster = gdal.Open(
        target_raster_path, gdal.OF_RASTER | gdal.GA_Update)
    target_band = target_raster.GetRasterBand(1)

    signal_raster = gdal.Open(signal_path_band[0], gdal.OF_RASTER)
    signal_band = signal_raster.GetRasterBand(signal_path_band[1])
    n_cols_signal = signal_band.XSize
    n_rows_signal = signal_band.YSize

    if mask_raster_path:
        mask_raster_info = get_raster_info(mask_raster_path)
        mask_nodata = mask_raster_info['nodata'][0]
        mask_raster = gdal.Open(
            mask_raster_path, gdal.OF_RASTER | gdal.GA_Update)
        mask_band = mask_raster.GetRasterBand(1)

    while True:
        print 'write worker getting payload'
        payload = write_raster_queue.get()
        if payload is None:
            break
        print 'write worker payload', payload
        (index_dict, result, mask_result, left_index_raster,
         right_index_raster, top_index_raster, bottom_index_raster) = payload
        index_tuple = (
            index_dict['xoff'],
            index_dict['yoff'],
            index_dict['win_xsize'],
            index_dict['win_ysize'],
        )
        print (index_dict, result, mask_result, left_index_raster,
               right_index_raster, top_index_raster, bottom_index_raster)

        left_index_result = 0
        right_index_result = result.shape[1]
        top_index_result = 0
        bottom_index_result = result.shape[0]

        # we might abut the edge of the raster, clip if so
        if left_index_raster < 0:
            left_index_result = -left_index_raster
            left_index_raster = 0
        if top_index_raster < 0:
            top_index_result = -top_index_raster
            top_index_raster = 0
        if right_index_raster > n_cols_signal:
            right_index_result -= right_index_raster - n_cols_signal
            right_index_raster = n_cols_signal
        if bottom_index_raster > n_rows_signal:
            bottom_index_result -= (
                bottom_index_raster - n_rows_signal)
            bottom_index_raster = n_rows_signal

        # read the current so we can add to it
        if index_tuple in target_processed_set:
            current_output = target_band.ReadAsArray(**index_dict)
        else:
            target_processed_set.add(index_tuple)
            current_output = numpy.zeros(
                (index_dict['win_ysize'], index_dict['win_xsize']))

        # read the signal block so we know where the nodata are
        potential_nodata_signal_array = signal_band.ReadAsArray(
            **index_dict)
        output_array = numpy.empty(
            current_output.shape, dtype=numpy.float32)

        valid_mask = numpy.ones(
            potential_nodata_signal_array.shape, dtype=bool)
        # guard against a None nodata value
        if signal_nodata is not None and mask_nodata:
            valid_mask[:] = (
                potential_nodata_signal_array != signal_nodata)
        output_array[:] = target_nodata
        output_array[valid_mask] = (
            (result[top_index_result:bottom_index_result,
                    left_index_result:right_index_result])[valid_mask] +
            current_output[valid_mask])

        target_band.WriteArray(
            output_array, xoff=index_dict['xoff'],
            yoff=index_dict['yoff'])

        if signal_nodata is not None and ignore_nodata:
            # we'll need to save off the mask convolution so we can divide
            # it in total later
            # read the current so we can add to it
            if index_tuple in mask_processed_set:
                current_mask = mask_band.ReadAsArray(**index_dict)
            else:
                mask_processed_set.add(index_tuple)
                current_mask = numpy.zeros(
                    (index_dict['win_ysize'], index_dict['win_xsize']))
            output_array[valid_mask] = (
                (mask_result[
                    top_index_result:bottom_index_result,
                    left_index_result:right_index_result])[valid_mask] +
                current_mask[valid_mask])
            mask_band.WriteArray(
                output_array, xoff=index_dict['xoff'],
                yoff=index_dict['yoff'])
