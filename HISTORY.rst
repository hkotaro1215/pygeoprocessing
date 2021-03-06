Release History
===============

1.2.1 (7/22/2018)
-----------------
* Fixing an issue with `warp_raster` that would round off bounding boxes
  for rasters that did not fit perfectly into the target raster's provided
  pixel size.
* Cautiously `join`ing all process pools to avoid a potential bug where a
  deamonized subprocess in a process pool may still have access to a raster
  but another process may require write access to it.

1.2.0 (7/19/2018)
-----------------

* Several PyGeoprocessing functions now take advantage of multiple CPU cores:
  * `raster_calculator` uses a separate thread to calculate raster statistics
     in a `nogil` section of Cython code. In timing with a big rasters we
     saw performance improvements of about 35%.
  * `align_and_resize_raster_stack` uses as many CPU cores, up to the number
    of CPUs reported by multiprocessing.cpu_count (but no less than 1), to
    process each raster warp while also accounting for the fact that
    `gdal.Warp` uses 2 cores on its own.
  * `warp_raster` now directly uses `gdal.Warp`'s multithreading directly.
    In practice it seems to utilize two cores.
  * `convolve_2d` attempts to use `multiprocessing.cpu_count` cpus to
    calculate separable convolutions per block while using the main thread to
    aggregate  and write the result to the target raster. In practice we saw
    this improve runtimes by about 50% for large rasters.
* Fixed a bug that caused some nodata values to not be treated as nodata
  if there was a numerical roundoff.
* A recent GDAL upgrade (might have been 2.0?) changed the reference to
  nearest neighbor interpolation from 'nearest' to 'near'. This PR changes
  PyGeoprocessing to be consistent with that change.
* ``raster_calculator`` can now also take "raw" arguments in the form of a
  (value, "raw") tuple. The parameter `value` will be passed directly to
  `local_op`. Scalars are no longer a special case and need to be passed as
  "raw" parameters.
* Raising `ValueError` in `get_raster_info` and `get_vector_info` in cases
  where non-filepath non-GIS values are passed as parameters. Previously
  such an error would result in an unhelpful error in the GDAL library.

1.1.0 (7/6/2018)
----------------
* PyGeoprocessing now supports Python 2 and 3, and is tested on python 2.7
  and 3.6  Testing across multiple versions is configured to be run via
  ``tox``.
* After testing (tox configuration included under ``tox-libcompat.ini``), numpy
  requirement has been dropped to ``numpy>=1.10.0`` and scipy has been modified
  to be ``scipy>=0.14.1,!=0.19.1``.
* A dependency on ``future`` has been added for compatibility between python
  versions.
* Fixed a crash in ``pygeoprocessing.routing.flow_dir_mfd`` and
  ``flow_dir_d8`` if a base raster was passed in that did not have a power of
  two blocksize.
* ``raster_calculator`` can now take numpy arrays and scalar values along with
  raster path band tuples. Arrays and scalars are broadcast to the raster size
  according to numpy array broadcasting rules.
* ``align_and_resize_raster_stack`` can now take a desired target projection
  which causes all input rasters to be warped to that projection on output.

1.0.1 (5/16/2018)
-----------------
* Hotfix patch to remove upper bound on required numpy version. This was
  causing a conflict with InVEST's looser requirement. Requirement is now
  set to >=1.13.0.

1.0.0 (4/29/2018)
-----------------
* This release marks a feature-complete version of PyGeoprocessing with a
  full suite of routing and geoprocessing capabilities.
* `pygeoprocessing.routing` module has a `flow_dir_mfd` function that
  calculates a 32 bit multiple flow direction raster.
* `pygeoprocessing.routing` module has a `flow_accumulation_mfd` function that
  uses the flow direction raster from `pygeoprocessing.routing.flow_dir_mfd`
  to calculate a per-pixel continuous flow accumulation raster.
* `pygeoprocessing.routing` module has a `distance_to_channel_mfd` function
  that calculates distance to a channel raster given a pygeoprocessing MFD
  raster.
* `pygeoprocessing.routing` module has a `distance_to_channel_d8` function
  that calculates distance to a channel raster given a pygeoprocessing D8
  raster.

0.7.0 (4/18/2018)
-----------------
* Versioning is now handled by ``setuptools_scm`` rather than
  ``natcap.versioner``.  ``pygeoprocessing.__version__`` is now fetched from
  the package metadata.
* Raster creation defaults now set "COMPRESS=LZW" for all rasters created in
  PyGeoprocessing, including internal temporary rasters. This option was
  chosen after profiling large raster creation runs on platter hard drives.
  In many cases processing time was dominated by several orders of magnitude
  as a write-to-disk. When compression is turned on overall runtime of very
  large rasters is significantly reduced. Note this otherwise increases the
  runtime small raster creation and processing by a small amount.
* `pygeoprocessing.routing` module now has a `fill_pits`, function which
   fills hydrological pits with a focus on runtime efficiency, memory space
   efficiency, and cache locality.
* `pygeoprocessing.routing` module has a `flow_dir_d8` that uses largest
  slope to determine the downhill flow direction.
* `pygeoprocessing.routing` module has a `flow_accumulation_d8` that uses
  a pygeoprocessing D8 flow direction raster to calculate per-pixel flow
  accumulation.
* Added a `merge_rasters` function to `pygeoprocessing` that will mosaic a
  set of rasters in the same projection, pixel size, and band count.

0.6.0 (1/10/2017)
-----------------
* Added an optional parameter to `iterblocks` to allow the `largest_block` to
  be set something other than the PyGeoprocessing default. This in turn
  allows the `largest_block` parameter in `raster_calculator` to be passed
  through to `iterblocks`.
* Upgraded PyGeoprocessing GDAL dependency to >=2.0.
* Added a `working_dir` optional parameter to `zonal_statistics`,
  `distance_transform_edt`, and `convolve_2d` which specifies a directory in
  which temporary files will be created during execution of the function.
  If set to `None` files are created in the default system temporary
  directory.

0.5.0 (9/14/2017)
-----------------
* Fixed an issue where NETCDF files incorrectly raised Exceptions in
  `raster_calculator`  and `rasterize` because they aren't filepaths.
* Added a NullHandler so that users wouldn't get an error that a logger
  handler was undefined.
* Added `ignore_nodata`, `mask_nodata`, and `normalize_kernel` options to
  `convolve_2d` which make this function capable of adapting the nodata
  overlap with the kernel rather than zero out the result, as well as on
  the fly normalization of the kernel for weighted averaging purposes. This
  is in part to make this functionality more consistent with ArcGIS's
  spatial filters.

0.4.4 (8/18/2017)
-----------------
* When testing for raster alignment `raster_calculator` no longer checks the
  string equality for projections or geotransforms.  Instead it only checks
  raster size equality.  This fixes issues where users rasters DO align, but
  have a slightly different text format of the WKT of projection.  It also
  abstracts the problem of georeferencing away from raster_calculator that is
  only a grid based operation.

0.4.3 (8/16/2017)
-----------------
* Changed the error message in `reclassify_raster` so it's more informative
  about how many values are missing and the values in the input lookup table.
* Added an optional parameter `target_nodata` to `convolve_2d` to set the
  desired target nodata value.

0.4.2 (6/20/2017)
-----------------
* Hotfix to fix an issue with `iterblocks` that would return signed values on
  unsigned raster types.
* Hotfix to correctly cite Natural Capital Project partners in license and
  update the copyright year.
* Hotfix to patch an issue that gave incorrect results in many PyGeoprocessing
  functions when a raster was passed with an NoData value.  In these cases the
  internal raster block masks would blindly pass through on the first row
  since a test for `numpy.ndarray == None` is `False` and later `x[False]`
  is the equivalent of indexing the first row of the array.

0.4.1 (6/19/2017)
-----------------
* Non-backwards compatible refactor of core PyGeoprocessing geoprocessing
  pipeline. This is to in part expose only orthogonal functionality, address
  runtime complexity issues, and follow more conventional GIS naming
  conventions. Changes include:
    * Full test coverage for `pygeoprocessing.geoprocessing` module
    * Dropping "uri" moniker in lieu of "path".
    * If a raster path is specified and operation requires a single band,
      argument is passed as a "(path, band)" tuple where the band index starts
      at 1 as convention for raster bands.
    * Shapefile paths are assumed to operate on the first layer.  It is so
      rare for a shapefile to have more than one layer, functions that would
      be confused by multiple layers have a layer_index that defaults to 0
      that can be overridden in the call.
    * Be careful, many of the parameter orders have been changed and renamed.
      Generally inputs come first, outputs last.  Input parameters are
      often prefixed with "base_" while output parameters are prefixed with
      "target_".
    * Functions that take rasters as inputs must have their rasters aligned
      before the call to that function.  The function
      `align_and_resize_raster_stack` can handle this.
    * `vectorize_datasets` refactored to `raster_calculator` since that name
      is often used as a convention when referring to raster calculations.
    * `vectorize_points` refactored to meaningful `interpolate_points`.
    * `aggregate_by_shapefile` refactored to `zonal_statistics` and now
      returns a dictionary rather than a named tuple.
    * All functions that create rasters expose the underlying GeoTIFF options
      through a default parameter `gtiff_creation_options` which default to
      "('TILED=YES', 'BIGTIFF=IF_SAFER')".
    * Individual functions for raster and vector properties have been
      aggregated into `get_raster_info` and `get_vector_info` respectively.
    * Introducing `warp_raster` to wrap GDAL's `ReprojectImage` functionality
      that also works on bounding box clips.
    * Removed the `temporary_filename()` paradigm.  Users should manage
      temporary filenames directly.
    * Numerous API changes from the 0.3.x version of PyGeoprocessing.
* Fixing an issue with aggregate_raster_values that caused a crash if feature
  IDs were not in increasing order starting with 0.
* Removed "create_rat/create_rat_uri" and migrated it to
  natcap.invest.wind_energy; the only InVEST model that uses that function.
* Fixing an issue with aggregate_raster_values that caused a crash if feature IDs were not in increasing order starting with 0.
* Removed "create_rat/create_rat_uri" and migrated it to natcap.invest.wind_energy; the only InVEST model that uses that function.

0.3.3 (2/9/2017)
----------------
* Fixing a memory leak with large polygons when calculating disjoint set.

0.3.2 (1/24/2017)
-----------------
* Hotfix to patch an issue with watershed delineation packing that causes some field values to lose precision due to default field widths being set.

0.3.1 (1/18/2017)
-----------------
* Hotfix patch to address an issue in watershed delineation that doesn't pack the target watershed output file.  Half the shapefile consists of features polygonalized around nodata values that are flagged for deletion, but not removed from the file.  This patch packs those features and returns a clean watershed.

0.3.0 (10/21/2016)
------------------
* Added `rel_tol` and `abs_tol` parameters to `testing.assertions` to be
  consistent with PEP485 and deal with real world testing situations that
  required an absolute tolerance.
* Removed calls to ``logging.basicConfig`` throughout pygeoprocessing.  Client
  applications may need to adjust their logging if pygeoprocessing's log
  messages are desired.
* Added a flag  to `aggregate_raster_values_uri` that can be used to indicate
  incoming polygons do not overlap, or the user does not care about overlap.
  This can be used in cases where there is a computational or memory
  bottleneck in calculating the polygon disjoint sets that would ultimately be
  unnecessary if it is known a priori that such a check is unnecessary.
* Fixed an issue where in some cases different nodata values for 'signal' and
  'kernel' would cause incorrect convolution results in `convolve_2d_uri`.
* Added functionality to `pygeoprocessing.iterblocks` to iterate over largest
  memory aligned block that fits into the number of elements provided by the
  parameter.  With default parameters, this uses a ceiling around 16MB of
  memory per band.
* Added functionality to `pygeoprocessing.iterblocks` to return only the
  offset dictionary.  This functionality would be used in cases where memory
  aligned writes are desired without first reading arrays from the band.
* Refactored `pygeoprocessing.convolve_2d_uri` to use `iterblocks` to take
  advantage of large block sizes for FFT summing window method.
* Refactoring source side to migrate source files from [REPO]/pygeoprocessing
  to [REPO]/src/pygeoprocessing.
* Adding a pavement script with routines to fetch SVN test data, build a
  virtual environment, and clean the environment in a Windows based operating
  system.
* Adding `transform_bounding_box` to calculate the largest projected bounding
  box given the four corners on a local coordinate system.
* Removing GDAL, Shapely from the hard requirements in setup.py.  This will
  allow pygeoprocessing to be built by package managers like pip without these
  two packages being installed.  GDAL and Shapely will still need to be
  installed for pygeoprocessing to run as expected.
* Fixed a defect in ``pygeoprocessing.testing.assert_checksums_equal``
  preventing BSD-style checksum files from being analyzed correctly.
* Fixed an issue in reclassify_dataset_uri that would cause an exception if
  the incoming raster didn't have a nodata value defined.
* Fixed a defect in ``pygeoprocessing.geoprocessing.get_lookup_from_csv``
  where the dialect was unable to be detected when analyzing a CSV that was
  larger than 1K in size.  This fix enables the correct detection of comma or
  semicolon delimited CSV files, so long as the header row by itself is not
  larger than 1K.
* Intra-package imports are now relative.  Addresses an import issue for users
  with multiple copies of pygeoprocessing installed across multiple Python
  installations.
* Exposed cython routing functions so they may be imported from C modules.
* `get_lookup_from_csv` attempts to determine the dialect of the CSV instead
  of assuming comma delimited.
* Added relative numerical tolerance parameters to the PyGeoprocessing raster
  and csv tests with in the same API style as `numpy.testing.allclose`.
* Fixed an incomparability with GDAL 1.11.3 bindings that expects a boolean
  type in `band.ComputeStatistics`.  Before this fix PyGeoprocessing would
  crash with a TypeError on many operations.
* Fixed a defect in pygeoprocessing.routing.calculate_transport where the
  nodata types were cast as int even though the base type of the routing
  rasters were floats.  In extreme cases this could cause a crash on a type
  that could not be converted to an int, like an `inf`, and in subtle cases
  this would result in nodata values in the raster being ignored during
  routing.
* Added functions to construct raster and vectors on disk from reasonable
  datatypes (numpy matrices for rasters, lists of Shapely geometries for
  vectors).
* Fixed an issue where reproject_datasource_uri would add geometry that
  couldn't be projected directly into the output datasource.  Function now
  only adds geometries that transformed without error and reports if any
  features failed to transform.
* Added file flushing and dataset swig deletion in reproject_datasource_uri to
  handle a race condition that might have been occurring.
* Fixed an issue when "None" was passed in on new raster creation that would
  attempt to directly set that value as the nodata value in the raster.
* Added basic filetype-specific assertions for many geospatial filetypes, and
  tests for these assertions.  These assertions are exposed in
  `pygeoprocessing.testing`.
* Pygeoprocessing package tests can be run by invoking
  `python setup.py nosetests`.  A subset of tests may also be run from an
  installed pygeoprocessing distribution by calling `pygeoprocessing.test()`.
* Fixed an issue with reclassify dataset that would occur when small rasters
  whose first memory block would extend beyond the size of the raster thus
  passing in "0" values in the out of bounds area. Reclassify dataset
  identified these as valid pixels, even though vectorize_datsets would mask
  them out later.  Now vectorize_datasets only passes memory blocks that
  contain valid pixel data to its kernel op.
* Added support for very small AOIs that result in rasters less than a pixel
  wide.  Additionally an `all_touched` flag was added to allow the
  ALL_TOUCHED=TRUE option to be passed to RasterizeLayer in the AOI mask
  calculation.
* Added watershed delineation routine to
  pygeoprocessing.routing.delineate_watershed.  Operates on a DEM and point
  shapefile, optionally snaps outlet points to nearest stream as defined by a
  thresholded flow accumulation raster and copies the outlet point fields into
  the constructed watershed shapefile.
* Fixing a memory leak in block caches that held on to dataset, band, and
  block references even after the object was destroyed.
* Add an option to route_flux that lets the current pixel's source be included
  in the flux, or not.  Previous version would include on the source no matter
  what.
* Now using natcap.versioner for versioning instead of local versioning logic.

0.2.2 (2015-05-07)
------------------

* Adding MinGW-specific compiler flags for statically linking pygeoprocessing
  binaries against libstdc++ and libgcc.  Fixes an issue on many user's
  computers when installing from a wheel on the Python Package Index without
  having two needed DLLs on the PATH, resulting in an ImportError on pygeoprocessing.geoprocessing_core.pyd.
* Fixing an issue with versioning where 'dev' was displayed instead of the
  version recorded in pygeoprocessing/__init__.py.
* Adding all pygeoprocessing.geoprocessing functions to
  pygeoprocessing.__all__, which allows those functions to appear when
  calling help(pygeoprocessing).
* Adding routing_core.pxd to the manifest.  This fixes an issue where some
  users were unable to compiler pygeoprocessing from source.

0.2.1 (2015-04-23)
------------------

* Fixed a bug on the test that determines if a raster should be memory
  blocked.  Rasters were not getting square blocked if the memory block was
  row aligned.  Now creates 256x256 blocks on rasters larger than 256x256.
* Updates to reclassify_dataset_uri to use numpy.digitize rather than Python
  loops across the number of keys.
* More informative error messages raised on incorrect bounding box mode.
* Updated docstring on get_lookup_from_table to indicate the headers are case
  insensitive.
* Added updates to align dataset list that report which dataset is being
  aligned.  This is helpful for logging feedback when many datasets are passed
  in that don't take long enough to get a report from the underlying reproject
  dataset function.
* pygeoprocessing.routing.routing_core includes pxd to be \`cimport`able from
  a Cython module.

0.2.0 (2015-04-14)
------------------

* Fixed a library wide issue relating to the underlying numpy types of
  GDT_Byte Datasets.  Now correctly identify the signed and unsigned versions
  and removed all instances where code used to mod byte data to unsigned data
  and correctly creates signed/unsigned byte datasets during resampling.
* Removed extract_band_and_nodata function since it exposes the underlying
  GDAL types.
* Removed reclassify_by_dictionary since reclassify_dataset_uri provided
  almost the same functionality and was widely used.
* Removed the class OrderedDict that was not used.
* Removed the function calculate_value_not_in_dataset since it loaded the
  entire dataset into memory and was not useful.

0.1.8 (2015-04-13)
------------------

* Fixed an issue on reclassifying signed byte rasters that had negative nodata
  values but the internal type stored for vectorize datasets was unsigned.

0.1.7 (2015-04-02)
------------------

* Package logger objects are now identified by python hierarchical package
  paths (e.g. pygeoprocessing.routing)
* Fixed an issue where rasters that had undefined nodata values caused
  striping in the reclassify_dataset_uri function.

0.1.6 (2015-03-24)
------------------

* Fixing LICENSE.TXT to .txt issue that keeps reoccurring.

0.1.5 (2015-03-16)
------------------

* Fixed an issue where int32 dems with INT_MIN as the nodata value were being
  treated as real DEM values because of an internal cast to a float for the
  nodata type, but a cast to double for the DEM values.
* Fixed an issue where flat regions, such as reservoirs, that could only drain
  off the edge of the DEM now correctly drain as opposed to having undefined
  flow directions.

0.1.4 (2015-03-13)
------------------

* Fixed a memory issue for DEMs on the order of 25k X 25k, still may have
  issues with larger DEMs.

0.1.3 (2015-03-08)
------------------

* Fixed an issue so tox correctly executes on the repository.
* Created a history file to document current and previous releases.
* Created an informative README.rst.

0.1.2 (2015-03-04)
------------------

* Fixing issue that caused "LICENSE.TXT not found" during pip install.

0.1.1 (2015-03-04)
------------------

* Fixing issue with automatic versioning scheme.

0.1.0 (2015-02-26)
------------------

* First release on PyPI.
