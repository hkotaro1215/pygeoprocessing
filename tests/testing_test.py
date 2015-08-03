import unittest
import os
import shutil
import glob
import json

from pygeoprocessing.tests import skipIfDataMissing
import pygeoprocessing.testing as testing
from pygeoprocessing.testing import data_storage
from pygeoprocessing.testing import test_writing
import pygeoprocessing as raster_utils

SVN_LOCAL_DIR = json.load(open(os.path.join(
    os.path.dirname(__file__), 'svn_config.json')))['local']
POLLINATION_DATA = os.path.join(SVN_LOCAL_DIR, 'pollination', 'samp_input')
SAMPLE_RASTERS = os.path.join(SVN_LOCAL_DIR, 'sample_rasters')
SAMPLE_VECTORS = os.path.join(SVN_LOCAL_DIR, 'sample_vectors')
CARBON_DATA = os.path.join(SVN_LOCAL_DIR, 'carbon', 'input')
REGRESSION_ARCHIVES = os.path.join(SVN_LOCAL_DIR, 'data_storage', 'regression')
WRITING_ARCHIVES = os.path.join(SVN_LOCAL_DIR, 'test_writing')
TEST_INPUT = os.path.join(SVN_LOCAL_DIR, 'data_storage', 'test_input')
TEST_OUT = os.path.join(SVN_LOCAL_DIR, 'test_out')
BASE_DATA = os.path.join(SVN_LOCAL_DIR, 'base_data')
REGRESSION_INPUT = os.path.join(SVN_LOCAL_DIR, 'testing_regression')

class TestWritingTest(unittest.TestCase):
    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_file_has_class_pass(self):
        test_file = os.path.join(WRITING_ARCHIVES, 'simple_test.py.txt')
        cls_exists = test_writing.file_has_class(test_file, 'ExampleClass')
        self.assertEqual(cls_exists, True)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_file_has_class_fail(self):
        test_file = os.path.join(WRITING_ARCHIVES, 'simple_test.py.txt')
        cls_exists = test_writing.file_has_class(test_file, 'BadClass')
        self.assertEqual(cls_exists, False)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_add_test_to_class(self):
        test_file = os.path.join(WRITING_ARCHIVES, 'simple_test.py.txt')
        new_file = os.path.join(TEST_OUT, 'simple_test_new.py.txt')
        shutil.copyfile(test_file, new_file)

        test_class_name = 'ExampleClass'
        test_func_name = 'test_new_func'
        in_archive_uri = 'input_archive.tar.gz'
        out_archive_uri = 'output_archive.tar.gz'
        module = 'natcap.invest.sample_model.script'
        test_writing.add_test_to_class(new_file, test_class_name,
            test_func_name, in_archive_uri, out_archive_uri, module)

        regression_file = os.path.join(WRITING_ARCHIVES,
            'completed_regression_test.py.txt')
        testing.assert_files(new_file, regression_file)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_add_test_to_new_class(self):
        test_file = os.path.join(WRITING_ARCHIVES, 'simple_test.py.txt')
        new_file = os.path.join(TEST_OUT, 'simple_test_new.py.txt')
        shutil.copyfile(test_file, new_file)

        test_class_name = 'ExampleNewClass'
        test_func_name = 'test_new_func'
        in_archive_uri = 'input_archive.tar.gz'
        out_archive_uri = 'output_archive.tar.gz'
        module = 'natcap.invest.sample_model.script'
        test_writing.add_test_to_class(new_file, test_class_name,
            test_func_name, in_archive_uri, out_archive_uri, module)

        regression_file = os.path.join(WRITING_ARCHIVES,
            'regression_new_class.py.txt')
        testing.assert_files(new_file, regression_file)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_add_test_to_class_importerror(self):
        test_file = os.path.join(WRITING_ARCHIVES, 'test_importerror.py.txt')
        new_file = os.path.join(TEST_OUT, 'test_importerror_new.py.txt')
        shutil.copyfile(test_file, new_file)

        test_class_name = 'ExampleClass'
        test_func_name = 'test_new_func'
        in_archive_uri = 'input_archive.tar.gz'
        out_archive_uri = 'output_archive.tar.gz'
        module = 'natcap.invest.sample_model.script'
        test_writing.add_test_to_class(new_file, test_class_name,
            test_func_name, in_archive_uri, out_archive_uri, module)

        regression_file = os.path.join(WRITING_ARCHIVES,
            'test_importerror_complete.py.txt')
        testing.assert_files(new_file, regression_file)

class DataStorageTest(unittest.TestCase):
    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_collect_parameters_simple(self):
        params = {
            'a': 1,
            'b': 2,
            'c': os.path.join(TEST_INPUT, 'LU.csv'),
        }

        archive_uri = os.path.join(TEST_OUT, 'archive')

        data_storage.collect_parameters(params, archive_uri)
        archive_uri = archive_uri + '.tar.gz'

        regression_archive_uri = os.path.join(REGRESSION_ARCHIVES,
            'collect_parameters_simple.tar.gz')

        testing.assert_archives(archive_uri, regression_archive_uri)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_collect_parameters_nested_dict(self):
        params = {
            'a': 1,
            'b': 2,
            'd': {
                'one': 1,
                'two': 2,
                'three': os.path.join(TEST_INPUT, 'Guild.csv')
            },
            'c': os.path.join(TEST_INPUT, 'LU.csv'),
        }

        archive_uri = os.path.join(TEST_OUT, 'archive_nested_dict')

        data_storage.collect_parameters(params, archive_uri)
        archive_uri = archive_uri + '.tar.gz'

        regression_archive_uri = os.path.join(REGRESSION_ARCHIVES,
            'simple_nested_dict.tar.gz')

        testing.assert_archives(archive_uri, regression_archive_uri)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_archive_geotiff(self):
        params = {
            'raster': os.path.join(TEST_INPUT, 'landuse_cur_200m.tif')
        }
        archive_uri = os.path.join(TEST_OUT, 'raster_geotiff')
        data_storage.collect_parameters(params, archive_uri)
        archive_uri += '.tar.gz'

        regression_archive_uri = os.path.join(REGRESSION_ARCHIVES,
            'raster_geotiff.tar.gz')
        testing.assert_archives(archive_uri, regression_archive_uri)


    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_archive_arc_raster_nice(self):
        params = {
            'raster': os.path.join(SAMPLE_RASTERS, 'precip')
        }

        archive_uri = os.path.join(TEST_OUT, 'raster_nice')
        data_storage.collect_parameters(params, archive_uri)

        archive_uri += '.tar.gz'
        regression_archive_uri = os.path.join(REGRESSION_ARCHIVES,
            'arc_raster_nice.tar.gz')
        testing.assert_archives(archive_uri, regression_archive_uri)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_archive_arc_raster_messy(self):
        params = {
            'raster': os.path.join(TEST_INPUT, 'messy_raster_organization',
                'hdr.adf')
        }

        archive_uri = os.path.join(TEST_OUT, 'raster_messy')
        data_storage.collect_parameters(params, archive_uri)

        archive_uri += '.tar.gz'
        regression_archive_uri = os.path.join(REGRESSION_ARCHIVES,
            'arc_raster_messy.tar.gz')
        testing.assert_archives(archive_uri, regression_archive_uri)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_archive_esri_shapefile(self):
        params = {
            'vector': os.path.join(SAMPLE_VECTORS, 'harv_samp_cur.shp')
        }

        archive_uri = os.path.join(TEST_OUT, 'vector_collected')
        data_storage.collect_parameters(params, archive_uri)

        archive_uri += '.tar.gz'
        regression_archive_uri = os.path.join(REGRESSION_ARCHIVES,
            'vector_collected.tar.gz')
        testing.assert_archives(archive_uri, regression_archive_uri)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_archive_pollination_input(self):
        params = {
            u'ag_classes': '67 68 71 72 73 74 75 76 78 79 80 81 82 83 84 85 88 90 91 92',
            u'do_valuation': True,
            u'farms_shapefile': os.path.join(TEST_INPUT, 'farms.shp'),
            u'guilds_uri': os.path.join(TEST_INPUT, 'Guild.csv'),
            u'half_saturation': 0.125,
            u'landuse_attributes_uri': os.path.join(TEST_INPUT, 'LU.csv'),
            u'landuse_cur_uri': os.path.join(SAMPLE_RASTERS, 'lulc_samp_cur', 'hdr.adf'),
            u'landuse_fut_uri': os.path.join(SAMPLE_RASTERS, 'lulc_samp_fut', 'hdr.adf'),
            u'results_suffix': 'suff',
            u'wild_pollination_proportion': 1.0,
            u'workspace_dir': os.path.join(TEST_OUT, 'pollination_workspace'),
        }

        archive_uri = os.path.join(TEST_OUT, 'pollination_input')
        data_storage.collect_parameters(params, archive_uri)

        archive_uri += '.tar.gz'
        regression_archive_uri = os.path.join(REGRESSION_ARCHIVES,
            'pollination_input.tar.gz')
        testing.assert_archives(archive_uri, regression_archive_uri)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_extract_archive(self):
        workspace = raster_utils.temporary_folder()
        archive_uri = os.path.join(REGRESSION_ARCHIVES,
            'pollination_input.tar.gz')
        input_folder = raster_utils.temporary_folder()
        parameters = data_storage.extract_parameters_archive(workspace,
            archive_uri, input_folder)

        self.maxDiff = None
        regression_params = {
            u'ag_classes': u'67 68 71 72 73 74 75 76 78 79 80 81 82 83 84 85 88 90 91 92',
            u'do_valuation': True,
            u'farms_shapefile': os.path.join(input_folder, u'vector_3ZRP4E'),
            u'guilds_uri': os.path.join(input_folder, u'Guild.csv'),
            u'half_saturation': 0.125,
            u'landuse_attributes_uri': os.path.join(input_folder, u'LU.csv'),
            u'landuse_cur_uri': os.path.join(input_folder, u'raster_UIMHR6'),
            u'landuse_fut_uri': os.path.join(input_folder, u'raster_6A6DEA'),
            u'results_suffix': u'suff',
            u'wild_pollination_proportion': 1.0,
            u'workspace_dir': workspace,
        }

        self.assertEqual(parameters, regression_params)

        for key in ['farms_shapefile', 'guilds_uri', 'landuse_attributes_uri',
            'landuse_cur_uri', 'landuse_fut_uri']:
            self.assertEqual(True, os.path.exists(parameters[key]))

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_extract_archive_nested_args(self):
        input_parameters = {
            'a': 1,
            'b': 2,
            'd': {
                'one': 1,
                'two': 2,
                'three': os.path.join(TEST_INPUT, 'Guild.csv')
            },
            'c': os.path.join(SAMPLE_VECTORS, 'harv_samp_cur.shp'),
            'raster_list': [
                os.path.join(SAMPLE_RASTERS, 'precip'),
                {
                    'lulc_samp_cur': os.path.join(SAMPLE_RASTERS, 'lulc_samp_cur'),
                    'do_biophysical': True,
                }
            ],
            'c_again': os.path.join(SAMPLE_VECTORS, 'harv_samp_cur.shp'),
        }
        archive_uri = os.path.join(TEST_OUT, 'nested_args')
        data_storage.collect_parameters(input_parameters, archive_uri)
        archive_uri += '.tar.gz'
        self.maxDiff=None

        workspace = raster_utils.temporary_folder()
        input_folder = raster_utils.temporary_folder()
        regression_parameters = {
            u'a': 1,
            u'b': 2,
            u'd': {
                u'one': 1,
                u'two': 2,
                u'three': os.path.join(input_folder, u'Guild.csv')
            },
            u'c': os.path.join(input_folder, u'vector_NCWK6A'),
            u'raster_list': [
                os.path.join(input_folder, u'raster_HD4O5B'),
                {
                    u'lulc_samp_cur': os.path.join(input_folder, u'raster_7ZA2EY'),
                    u'do_biophysical': True,
                }
            ],
            u'c_again': os.path.join(input_folder, u'vector_NCWK6A'),
            u'workspace_dir': workspace,
        }
        parameters = data_storage.extract_parameters_archive(workspace,
            archive_uri, input_folder)
        self.assertEqual(parameters, regression_parameters)

        files_to_check = [
            regression_parameters['d']['three'],
            regression_parameters['c'],
            regression_parameters['raster_list'][0],
            regression_parameters['raster_list'][1]['lulc_samp_cur'],
            regression_parameters['c_again'],
        ]

        for file_uri in files_to_check:
            self.assertEqual(True, os.path.exists(file_uri))

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_archive_dbf(self):
        input_parameters = {
            'dbf_file': os.path.join(TEST_INPUT, 'carbon_pools_samp.dbf'),
        }
        archive_uri = os.path.join(TEST_OUT, 'dbf_archive')
        data_storage.collect_parameters(input_parameters, archive_uri)

        archive_uri += '.tar.gz'
        reg_archive_uri = os.path.join(REGRESSION_ARCHIVES,
            'dbf_archive.tar.gz')
        testing.assert_archives(archive_uri, reg_archive_uri)


class GISTestTester(unittest.TestCase):
    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_raster_assertion_fileio(self):
        """Verify correct behavior for assertRastersEqual"""

        # check that IOError is raised if a file is not found.
        raster_on_disk = os.path.join(POLLINATION_DATA, 'landuse_cur_200m.tif')
        self.assertRaises(IOError, testing.assert_rasters_equal, 'file_not_on_disk',
            'other_file_not_on_disk')
        self.assertRaises(IOError, testing.assert_rasters_equal, 'file_not_on_disk',
            raster_on_disk)
        self.assertRaises(IOError, testing.assert_rasters_equal,
            raster_on_disk, 'file_not_on_disk')
        testing.assert_rasters_equal(raster_on_disk, raster_on_disk)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_raster_assertion_files_equal(self):
        """Verify when rasters are, in fact, equal."""
        temp_folder = raster_utils.temporary_folder()
        new_raster = os.path.join(temp_folder, 'new_file.tif')

        source_file = os.path.join(POLLINATION_DATA, 'landuse_cur_200m.tif')
        shutil.copyfile(source_file, new_raster)
        testing.assert_rasters_equal(source_file, new_raster)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_raster_assertion_different_dims(self):
        """Verify when rasters are different"""
        source_raster = os.path.join(POLLINATION_DATA, 'landuse_cur_200m.tif')
        different_raster = os.path.join(BASE_DATA, 'terrestrial',
            'lulc_samp_cur')
        self.assertRaises(AssertionError, testing.assert_rasters_equal,
            source_raster, different_raster)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_raster_assertion_different_values(self):
        """Verify when rasters have different values"""
        lulc_cur_raster = os.path.join(POLLINATION_DATA, 'landuse_cur_200m.tif')
        lulc_fut_raster = os.path.join(POLLINATION_DATA, 'landuse_fut_200m.tif')
        self.assertRaises(AssertionError, testing.assert_rasters_equal,
            lulc_cur_raster, lulc_fut_raster)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_vector_assertion_fileio(self):
        """Verify correct behavior for assertVectorsEqual"""
        vector_on_disk = os.path.join(POLLINATION_DATA, 'farms.dbf')
        self.assertRaises(IOError, testing.assert_rasters_equal, 'file_not_on_disk',
            'other_file_not_on_disk')
        self.assertRaises(IOError, testing.assert_rasters_equal, 'file_not_on_disk',
            vector_on_disk)
        self.assertRaises(IOError, testing.assert_rasters_equal,
            vector_on_disk, 'file_not_on_disk')
        testing.assert_vectors_equal(vector_on_disk, vector_on_disk)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_vector_assertion_files_equal(self):
        """Verify when vectors are equal."""
        temp_folder = raster_utils.temporary_folder()
        for vector_file in glob.glob(os.path.join(POLLINATION_DATA, 'farms.*')):
            base_name = os.path.basename(vector_file)
            new_file = os.path.join(temp_folder, base_name)
            shutil.copyfile(vector_file, new_file)

        sample_shape = os.path.join(POLLINATION_DATA, 'farms.shp')
        copied_shape = os.path.join(temp_folder, 'farms.shp')
        testing.assert_vectors_equal(sample_shape, copied_shape)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_vectors_different_attributes(self):
        """Verify when two vectors have different attributes"""
        base_file = os.path.join(POLLINATION_DATA, 'farms.shp')
        different_file = os.path.join(POLLINATION_DATA, '..',
            'biophysical_output', 'farms_abundance_cur', 'farms.shp')

        self.assertRaises(AssertionError, testing.assert_vectors_equal, base_file,
            different_file)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_vectors_very_different(self):
        """Verify when two vectors are very, very different."""
        base_file = os.path.join(POLLINATION_DATA, 'farms.shp')
        different_file = os.path.join(CARBON_DATA, 'harv_samp_cur.shp')
        self.assertRaises(AssertionError, testing.assert_vectors_equal, base_file,
            different_file)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_csv_assertion_fileio(self):
        bad_file_1 = 'aaa'
        bad_file_2 = 'bbbbb'
        good_file = os.path.join(POLLINATION_DATA, 'Guild.csv')

        self.assertRaises(IOError, testing.assert_csv_equal, bad_file_1, bad_file_2)
        self.assertRaises(IOError, testing.assert_csv_equal, bad_file_1, good_file)
        self.assertRaises(IOError, testing.assert_csv_equal, good_file, bad_file_2)
        testing.assert_csv_equal(good_file, good_file)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_csv_assertion_fails(self):
        sample_file = os.path.join(POLLINATION_DATA, 'Guild.csv')
        different_file = os.path.join(POLLINATION_DATA, 'LU.csv')

        self.assertRaises(AssertionError, testing.assert_csv_equal, sample_file,
            different_file)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_md5_same(self):
        """Check that the MD5 is equal."""
        test_file = os.path.join(CARBON_DATA, 'harv_samp_cur.shp')
        md5_sum = testing.get_hash(test_file)
        testing.assert_md5(test_file, md5_sum)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_md5_different(self):
        """Check that the MD5 is equal."""
        test_file = os.path.join(CARBON_DATA, 'harv_samp_cur.shp')
        md5_sum = 'bad md5sum!'

        self.assertRaises(AssertionError, testing.assert_md5, test_file, md5_sum)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_archive_assertion(self):
        """Check that two archives are equal"""
        archive_file = os.path.join(REGRESSION_ARCHIVES,
            'arc_raster_nice.tar.gz')
        testing.assert_archives(archive_file, archive_file)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_archive_assertion_fails(self):
        """Check that assertion fails when two archives are different"""
        archive_file = os.path.join(REGRESSION_ARCHIVES,
            'arc_raster_nice.tar.gz')
        different_archive = os.path.join(REGRESSION_ARCHIVES,
            'arc_raster_messy.tar.gz')
        self.assertRaises(AssertionError, testing.assert_archives, archive_file,
            different_archive)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_workspaces_passes(self):
        """Check that asserting equal workspaces passes"""
        workspace_uri = os.path.join(REGRESSION_ARCHIVES, '..')
        testing.assert_workspace(workspace_uri, workspace_uri)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_workspaces_differ(self):
        """Check that asserting equal workspaces fails."""
        self.assertRaises(AssertionError, testing.assert_workspace,
            POLLINATION_DATA, REGRESSION_ARCHIVES)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_workspaces_ignore(self):
        """Check that ignoring certain files works as expected."""
        new_folder = raster_utils.temporary_folder()
        shutil.copytree(POLLINATION_DATA, new_folder)

        # make a file in POLLINATION_DATA by opening a writeable file there.
        copied_filepath = os.path.join(POLLINATION_DATA, 'test_file.txt')
        fp = open(copied_filepath, 'w')
        fp.close()


    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_json_same(self):
        """Check that asserting equal json objects passes."""
        json_path = os.path.join('invest-data/test/data', 'testing_regression',
            'sample_json.json')
        testing.assert_json(json_path, json_path)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_json_different(self):
        """Check that asserting different json objects fails"""
        json_path = os.path.join('invest-data/test/data', 'testing_regression',
            'sample_json.json')
        json_path_new = os.path.join('invest-data/test/data', 'testing_regression',
            'sample_json_2.json')
        self.assertRaises(AssertionError, testing.assert_json, json_path,
            json_path_new)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_assert_files_ext_diff(self):
        uri_1 = os.path.join('invest-data/test/data', 'testing_regression', 'sample_json.json')
        uri_2 = os.path.join(REGRESSION_ARCHIVES, 'arc_raster_nice.tar.gz')
        self.assertRaises(AssertionError, testing.assert_files, uri_1, uri_2)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_assert_files_dne(self):
        uri_1 = os.path.join('invest-data/test/data', 'file_not_exists.txt')
        uri_2 = os.path.join(REGRESSION_ARCHIVES, 'arc_raster_nice.tar.gz')
        self.assertRaises(AssertionError, testing.assert_files, uri_1, uri_2)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_assert_files_json_same(self):
        json_path = os.path.join('invest-data/test/data', 'testing_regression',
            'sample_json.json')
        testing.assert_files(json_path, json_path)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_assert_files_json_different(self):
        """Check that asserting different json objects fails"""
        json_path = os.path.join('invest-data/test/data', 'testing_regression',
            'sample_json.json')
        json_path_new = os.path.join('invest-data/test/data', 'testing_regression',
            'sample_json_2.json')
        self.assertRaises(AssertionError, testing.assert_files, json_path,
            json_path_new)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_assert_files_gdal_same(self):
        source_file = os.path.join(POLLINATION_DATA, 'landuse_cur_200m.tif')
        testing.assert_files(source_file, source_file)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_assert_files_gdal_different(self):
        source_raster = os.path.join(POLLINATION_DATA, 'landuse_cur_200m.tif')
        different_raster = os.path.join(BASE_DATA, 'terrestrial',
            'lulc_samp_cur')
        self.assertRaises(AssertionError, testing.assert_files,
            source_raster, different_raster)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_assert_files_ogr_same(self):
        sample_shape = os.path.join(POLLINATION_DATA, 'farms.shp')
        testing.assert_files(sample_shape, sample_shape)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_assert_files_ogr_different(self):
        base_file = os.path.join(POLLINATION_DATA, 'farms.shp')
        different_file = os.path.join(POLLINATION_DATA, '..',
            'biophysical_output', 'farms_abundance_cur', 'farms.shp')
        self.assertRaises(AssertionError, testing.assert_files, base_file,
            different_file)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_assert_text_same(self):
        """Check that asserting two identical text files passes"""
        sample_file = os.path.join(REGRESSION_INPUT, 'sample_text_file.txt')
        self.assertTextEqual(sample_file, sample_file)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_assert_text_different(self):
        """Check that asserting two different text files fails."""
        sample_file = os.path.join(REGRESSION_INPUT, 'sample_text_file.txt')
        regression_file = os.path.join(REGRESSION_INPUT, 'sample_json.json')
        self.assertRaises(AssertionError, self.assertTextEqual, sample_file,
            regression_file)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_assert_files_text_same(self):
        """Check that asserting two identical text files passes"""
        sample_file = os.path.join(REGRESSION_INPUT, 'sample_text_file.txt')
        testing.assert_files(sample_file, sample_file)

    @skipIfDataMissing(SVN_LOCAL_DIR)
    def test_assert_files_text_different(self):
        """Check that asserting two different text files fails."""
        sample_file = os.path.join(REGRESSION_INPUT, 'sample_text_file.txt')
        regression_file = os.path.join(REGRESSION_INPUT, 'sample_json.json')
        self.assertRaises(AssertionError, testing.assert_files, sample_file,
            regression_file)
