"""setup.py module for PyGeoprocessing."""
from Cython.Build import cythonize
import numpy
from setuptools.extension import Extension
from setuptools import setup

# Read in requirements.txt and populate the python readme with the non-comment
# contents.
_REQUIREMENTS = [
    x for x in open('requirements.txt').read().split('\n')
    if not x.startswith('#') and len(x) > 0]
README = open('README.rst').read().format(
    requirements='\n'.join(['    ' + r for r in _REQUIREMENTS]))

setup(
    name='pygeoprocessing',
    description="PyGeoprocessing: Geoprocessing routines for GIS",
    long_description=README,
    maintainer='Rich Sharp',
    maintainer_email='richpsharp@gmail.com',
    url='https://bitbucket.org/natcap/pygeoprocessing',
    packages=[
        'pygeoprocessing',
        'pygeoprocessing.routing',
        'pygeoprocessing.testing',
    ],
    package_dir={
        'pygeoprocessing': 'src/pygeoprocessing'
    },
    use_scm_version={
        'version_scheme': 'post-release',
        'local_scheme': 'node-and-date'},
    setup_requires=['setuptools_scm', 'cython', 'numpy'],
    include_package_data=True,
    install_requires=_REQUIREMENTS,
    license='BSD',
    zip_safe=False,
    keywords='gis pygeoprocessing',
    classifiers=[
        'Intended Audience :: Developers',
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Science/Research',
        'Natural Language :: English',
        'Operating System :: MacOS :: MacOS X',
        'Operating System :: Microsoft',
        'Operating System :: POSIX',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.6',
        'Topic :: Scientific/Engineering :: GIS',
        'License :: OSI Approved :: BSD License'
    ],
    ext_modules=cythonize(
        [Extension(
            "pygeoprocessing.routing.routing",
            sources=["src/pygeoprocessing/routing/routing.pyx"],
            include_dirs=[
                numpy.get_include(),
                'src/pygeoprocessing/routing'],
            language="c++",
        ),
         Extension(
             "pygeoprocessing.geoprocessing_core",
             sources=[
                 'src/pygeoprocessing/geoprocessing_core.pyx'],
             include_dirs=[numpy.get_include()],
             language="c++")],
        compiler_directives={'linetrace': True, 'binding': True},
        )
)
