# coding: utf-8
'''
Created on 2014-08-20

A module to load station daily data from Environment Canada from ASCII files and convert them to monthly 
NetCDF datasets (with extremes); the module also provides a wrapper to load the NetCDF datasets. 

@author: Andre R. Erler, GPL v3
'''

# external imports
from collections import OrderedDict
import numpy as np
import numpy.ma as ma
import codecs
# internal imports
from datasets.common import days_per_month, name_of_month, data_root
from geodata.misc import ParseError, DateError, VariableError, RecordClass, StrictRecordClass
from geodata.gdal import Shape
from geodata.station import StationDataset, Variable, Axis
# from geodata.misc import DatasetError
from warnings import warn

## EC (Environment Canada) Meta-data

dataset_name = 'EC'
root_folder = data_root + dataset_name + '/'
orig_ts_file = '{0:s}{1:s}.txt' # filename pattern: variable name and station ID
tsfile = 'ec{0:s}_monthly.nc' # filename pattern: station type 
avgfile = 'ec{0:s}_clim{1:s}.nc' # filename pattern: station type and ('_'+period)
avgfolder = root_folder + 'ecavg/'  # folder for user data

# variable attributes and name
varatts = dict(T2       = dict(name='T2', units='K', atts=dict(long_name='Average 2m Temperature')), # 2m average temperature
               Tmin     = dict(name='Tmin', units='K', atts=dict(long_name='Minimum 2m Temperature')), # 2m minimum temperature
               Tmax     = dict(name='Tmax', units='K', atts=dict(long_name='Maximum 2m Temperature')), # 2m maximum temperature
               precip   = dict(name='precip', units='kg/m^2/s', atts=dict(long_name='Total Precipitation')), # total precipitation
               solprec  = dict(name='solprec', units='kg/m^2/s', atts=dict(long_name='Solid Precipitation')), # solid precipitation
               liqprec  = dict(name='liqprec', units='kg/m^2/s', atts=dict(long_name='Liquid Precipitation')), # liquid precipitation
               # meta/constant data variables
               name  = dict(name='name', units='', atts=dict(long_name='Station Name')), # the proper name of the station
               prov  = dict(name='prov', units='', atts=dict(long_name='Province')), # in which Canadian Province the station is located
               lat  = dict(name='lat', units='deg N', atts=dict(long_name='Latitude')), # geographic latitude field
               lon  = dict(name='lon', units='deg E', atts=dict(long_name='Longitude')), # geographic longitude field
               alt  = dict(name='zs', units='m', atts=dict(long_name='Station Elevation')), # station elevation
               begin_date = dict(name='begin_date', units='month', atts=dict(long_name='Month since 1979-01', # begin of station record
                                                                             description='Begin of Station Record (relative to 1979-01)')), 
               end_date = dict(name='end_date', units='month', atts=dict(long_name='Month since 1979-01', # begin of station record
                                                                         description='End of Station Record (relative to 1979-01)')),
               # axes (also sort of meta data)
               time     = dict(name='time', units='month', atts=dict(long_name='Month since 1979-01')), # time coordinate
               station  = dict(name='station', units='#', atts=dict(long_name='Station Number'))) # ordinal number of station
# list of variables to load
variable_list = varatts.keys() # also includes coordinate fields    


## a class that handles access to station records in ASCII files
class DailyStationRecord(StrictRecordClass):
  '''
    A class that is used by StationRecords to facilitate access to daily station records from ASCII files.  
  '''
  # list of station parameters that need to be supplied
  id         = '' # station ID
  name       = '' # station name
  variable   = '' # variable name (full name used in header)
  units      = '' # data units used in record
  dtype      = '' # data type (default: float32)
  missing    = '' # string indicating missing value
  flags      = '' # legal data flags (case sensitive)
  varmin     = 0. # smallest allowed value in data
  varmax     = 0. # largest allowed value in data
  filename   = '' # absolute path of ASCII file containing data record
  encoding   = '' # text file encoding
  prov       = '' # province (in Canada)  
  joined     = False # whether station record was merged with another nearby  
  begin_year = 0 # year of first record
  begin_mon  = 0 # month of first record
  end_year   = 0 # year of last record 
  end_mon    = 0 # month of last record
  lat        = 0. # latitude of station location
  lon        = 0. # longitude of station location
  alt        = 0. # station elevation (altitude)
  
  # id='', name='', datatype='', filename='', prov='', begin_year=0, begin_mon=0, end_year=0, end_mon=0, lat=0, lon=0
      
  def validateHeader(self, headerline):
    ''' validate header information against stored meta data '''
    # parse header line (print header if an error occurs)
    header = [elt.strip().lower() for elt in headerline.split(',')]
    if self.id.lower() != header[0]: raise ParseError, headerline # station ID
    if self.name.lower() != header[1]: raise ParseError, headerline # station name
    if self.prov.lower() != header[2]: raise ParseError, headerline # province
    if 'joined' not in header[3]: raise ParseError, headerline # station joined or not
    else:
      if self.joined and 'not' in header[3]: raise ParseError, headerline # station joined or not
      if not self.joined and 'not' not in header[3]: raise ParseError, headerline # station joined or not
    if 'daily' not in header[4]: raise ParseError, headerline # this class only deals with daily values
    if self.variable.lower() not in header[4]: raise ParseError, headerline # variable name
    if self.units.lower() not in header[5]: raise ParseError, headerline # variable units
    # if no error was raised, we are good
    
  def checkHeader(self):
    ''' open the station file and validate the header information; then close '''
    # open file
    f = codecs.open(self.filename, 'r', encoding=self.encoding)
    self.validateHeader(f.readline()) # read first line as header
    f.close()
  
  def parseRecord(self):
    ''' open the station file and parse records; return a daiy time-series '''
    # open file
    f = codecs.open(self.filename, 'r', encoding=self.encoding)
    self.validateHeader(f.readline()) # read first line as header
    # allocate daily data array (31 days per month, filled with NaN for missing values)
    tlen = ( (self.end_year - self.begin_year) * 12 + (self.end_mon - self.begin_mon +1) ) * 31
    data = np.zeros((tlen,), dtype=self.dtype) # only three significant digits...
    data[:] = np.NaN # use NaN as missing values
    # some stuff to remember
    lfloat = 'float' in self.dtype; lint = 'int' in self.dtype; lm = len(self.missing)
    # iterate over line
    oldyear = self.begin_year; oldmon = self.begin_mon -1; z = 0
    for line in f:      
      ll = line.replace('-9999.9', ' -9999.9').split() # without the replace, the split doesn't work
      if ll[0].isdigit() and ll[1].isdigit():
        year = int(ll[0]); mon = int(ll[1])
        # check continuity
        if year == oldyear and mon == oldmon+1: pass
        elif year == oldyear+1 and oldmon == 12 and mon ==1: pass 
        else: raise DateError, line
        oldyear = year; oldmon = mon
#         # rigorous check of date bounds
#         if year == self.begin_year and mon < self.begin_mon: raise DateError, line
#         elif year < self.begin_year: raise DateError, line
#         if year == self.end_year and mon > self.end_mon: raise DateError, line
#         elif year > self.end_year: raise DateError, line
        # skip dates outside the specified begin/end dates
        if year < self.begin_year or year > self.end_year: pass # outside range 
        elif year == self.begin_year and mon < self.begin_mon: pass # start later
        elif year == self.end_year and mon > self.end_mon: pass # basically done
        # parse values
        if len(ll[2:]) > 5: # need more than 5 valid values
          zz = z 
          for num in ll[2:]:
            if num[:lm] == self.missing: pass # missing value; already pre-filled NaN;  or num[-1] == 'M'
            else:
              if lfloat and '.' in num and 1 < len(num): # at least 1 digit plus decimal, i.e. ignore the flag
                if num[-1].isdigit(): n = float(num)
                elif num[-2].isdigit() and num[-1] in self.flags: n = float(num[:-1]) # remove data flag
                else: raise ParseError, "Unable to process value '{:s}' in line:\n {:s}".format(num,line)
              elif lint and 0 < len(num):# almost the same as for floats
                if num[-1].isdigit(): n = int(num)
                elif num[-2].isdigit() and num[-1] in self.flags: n = int(num[:-1]) # remove data flag
                else: raise ParseError, "Unable to process value '{:s}' in line:\n {:s}".format(num,line)
              else: raise ParseError, "Unable to process value '{:s}' in line:\n {:s}".format(num,line)
              if n < self.varmin: raise ParseError, "Encountered value '{:s}' below minimum in line:\n {:s}".format(num,line)
              if n > self.varmax: raise ParseError, "Encountered value '{:s}' above maximum in line:\n {:s}".format(num,line)
              data[zz] = n
            zz += 1
          if zz != z+31: raise ParseError, 'Line has {:d} values instead of 31:\n {:s}'.format(zz-z,line)  
        # increment counter
        z += 31
      elif ll[0] != 'Year' or ll[1] != 'Mo':
        raise ParseError, "No valid title or data found at begining of file:\n {:s}".format(self.filename)
    if z < tlen: raise ParseError, 'Reached end of file before specified end date: {:s}'.format(self.filename)
#     if z != tlen: raise ParseError, 'Number of lines in file is inconsistent with begin and end date: {:s}'.format(self.filename)
    # close again
    f.close()
    # return array
    return data
  

## class that defines variable properties (specifics are implemented in children)
class VarDef(RecordClass):
  # variable specific
  name     = '' # full variable name
  atts     = None # dictionary with PyGeoData variable attributes
  prefix   = '' # file prefix
  # type specific
  datatype = '' # defined in child class
  units    = '' # units used for data  
  dtype    = 'float32' # data type used for data
  encoding = 'UTF-8' # file encoding
  missing  = '' # string indicating missing value
  flags    = '' # legal data flags (case sensitive)
  varmin   = 0. # smallest allowed value in data
  varmax   = 0. # largest allowed value in data
  # inferred variables
  variable = '' # alias for name
  filepath = '' # inferred from prefix
  
  def __init__(self, **kwargs):
    super(VarDef,self).__init__(**kwargs)
    self.variable = self.name
    self.filepath = '{0:s}/{0:s}{1:s}.txt'.format(self.prefix,'{:s}')
    
  def convert(self, data): return data # needs to be implemented by child
  
  def getKWargs(self, *args):
    ''' Return a dictionary with the specified arguments and their values '''
    if len(args) == 0: 
      args = ['variable', 'units', 'varmin', 'varmax', 'missing', 'flags', 'dtype', 'encoding']
    kwargs = dict()
    for arg in args:
      kwargs[arg] = getattr(self,arg)
    return kwargs
  
# definition for precipitation files
class PrecipDef(VarDef):
  units    = 'mm'
  datatype = 'precip'  
  missing  = '-9999.99' # string indicating missing value (apparently not all have 'M'...)
  flags    = 'TEFACLXYZ' # legal data flags (case sensitive; 'M' for missing should be screened earlier)
  varmin   = 0. # smallest allowed value in data
  varmax   = 1.e3 # largest allowed value in data
  
# definition for temperature files
class TempDef(VarDef):
  units    = u'°C'
  datatype = 'temp'
  encoding = 'ISO-8859-15' # for some reason temperature files have a strange encodign scheme...
  missing  = '-9999.9' # string indicating missing value
  flags    = 'Ea' # legal data flags (case sensitive; 'M' for missing should be screened earlier)
  varmin   = -100. # smallest allowed value in data
  varmax   = 100. # largest allowed value in data
  
  def convert(self, data): return data + 273.15 # convert to Kelvin

# definition of station meta data format 
EC_header_format = ('No','StnId','Prov','From','To','Lat(deg)','Long(deg)','Elev(m)','Joined','Station','name') 
EC_station_format = tuple([(None, int), 
                          ('id', str),                            
                          ('prov', str),                        
                          ('begin_year', int),     
                          ('begin_mon', int),
                          ('end_year', int),   
                          ('end_mon', int),
                          ('lat', float),                     
                          ('lon', float),                       
                          ('alt', float),                       
                          ('joined', lambda l: l.upper() == 'Y'),
                          ('name', str),])
    
## class to read station records and return a dataset
class StationRecords(object):
  '''
    A class that provides methods to load station data and associated meta data from files of a given format;
    The format itself will be defines in child classes.
    The data will be converted to monthly statistics and accessible as a PyGeoData dataset or can be written 
    to a NetCDF file.
  '''
  # arguments
  folder      = '' # root folder for station data: interval and datatype
  stationfile = 'stations.txt' # file that contains station meta data (to load station records)
  encoding    = '' # encoding of station file
  interval    = '' # source data interval (currently only daily)
  datatype    = '' # variable class, e.g. temperature or precipitation tyes
  variables   = None # parameters and definitions associated with variables
  header_format  = '' # station format definition (for validation)
  station_format = '' # station format definition (for reading)
  constraints    = None # constraints to limit the number of stations that are loaded
  # internal variables
  stationlists   = None # list of station objects
  dataset        = None # PyGeoData Dataset (will hold results) 
  
  def __init__(self, folder='', stationfile='stations.txt', variables=None, encoding='', interval='daily', 
               header_format=None, station_format=None, constraints=None):
    ''' Parse station file and initialize station records. '''
    # some input checks
    if not isinstance(stationfile,basestring): raise TypeError
    if interval != 'daily': raise NotImplementedError
    if header_format is None: header_format = EC_header_format # default
    elif not isinstance(header_format,(tuple,list)): raise TypeError
    if station_format is None: station_format = EC_station_format # default    
    elif not isinstance(station_format,(tuple,list)): raise TypeError
    if not isinstance(constraints,dict) and constraints is not None: raise TypeError
    if not isinstance(variables,dict): raise TypeError
    datatype = variables.values()[0].datatype
    if not all([var.datatype == datatype for var in variables.values()]): raise VariableError
    encoding = encoding or variables.values()[0].encoding 
    if not isinstance(encoding,basestring): raise TypeError
    folder = folder or '{:s}/{:s}_{:s}/'.format(root_folder,interval,datatype) # default folder scheme 
    if not isinstance(folder,basestring): raise TypeError
    # save arguments
    self.folder = folder
    self.stationfile = stationfile
    self.encoding = encoding
    self.interval = interval
    self.datatype = datatype
    self.variables = variables
    self.header_format = header_format
    self.station_format = station_format
    self.constraints = constraints
    ## initialize station objects from file
    # open and parse station file
    stationfile = '{:s}/{:s}'.format(folder,stationfile)
    f = codecs.open(stationfile, 'r', encoding=encoding)
    # initialize station objects and add to list
    header = f.readline() # read first line of header (title)
    if not datatype.lower() in header.lower(): raise ParseError
    f.readline() # discard second line (French)
    header = f.readline() # read third line (column definitions)
    for key,col in zip(header_format,header.split()):
      if key.lower() != col.lower(): 
        raise ParseError, "Column headers do not match format specification: {:s} != {:s} \n {:s}".format(key,col,header)
    f.readline() # discard forth line (French)    
    # initialize station list
    self.stationlists = {varname:[] for varname in variables.iterkeys()} # a separate list for each variable
    z = 0 # row counter 
    ns = 0 # station counter
    # loop over lines (each defiens a station)
    for line in f:
      z += 1 # increment counter
      collist = line.split()
      stdef = dict() # station specific arguments to instantiate station object
      # loop over column titles
      zz = 0 # column counter
      for key,fct in station_format[:-1]: # loop over columns
        if key is None: # None means skip this column
          if zz == 0: # first column
            if z != fct(collist[zz]): raise ParseError, "Station number is not consistent with line count."
        else:
          #print key, z, collist[zz]
          stdef[key] = fct(collist[zz]) # convert value and assign to argument
        zz += 1 # increment column
      assert zz <= len(collist) # not done yet
      # collect all remaining elements
      key,fct = station_format[-1]
      stdef[key] = fct(' '.join(collist[zz:]))
      #print z,stdef[key]
      # check station constraints
      if constraints is None: ladd = True
      else:
        ladd = True
        for key,val in constraints.iteritems():
          if stdef[key] not in val: ladd = False
      # instantiate station objects for each variable and append to lists
      if ladd:
        ns += 1
        # loop over variable definitions
        for varname,vardef in variables.iteritems():
          filename = '{0:s}/{1:s}'.format(folder,vardef.filepath.format(stdef['id']))
          kwargs = dict() # combine station and variable attributes
          kwargs.update(stdef); kwargs.update(vardef.getKWargs())
          station = DailyStationRecord(filename=filename, **kwargs)
          station.checkHeader() 
          self.stationlists[varname].append(station)
    assert len(self.stationlists[varname]) == ns # make sure we got all (lists should have the same length)
    
  def prepareDataset(self):
    ''' prepare a PyGeoData dataset for the station data (with all the meta data) '''
    from geodata import Axis, Variable, Dataset
    # meta data arrays
    dataset = Dataset(varlist=[])
    # station axis (by ordinal number)
    stationlist = self.stationlists.values()[0] # just use first list, since meta data is the same
    assert all([len(stationlist) == len(stnlst) for stnlst in self.stationlists.values()]) # make sure none is missing
    station = Axis(coord=np.arange(1,len(stationlist)+1, dtype='int16'), atts=varatts['station']) # start at 1
    # station name
    namelen = max([len(stn.name) for stn in stationlist])
    strarray = np.array([stn.name.ljust(namelen) for stn in stationlist], dtype='|S{:d}'.format(namelen))
    dataset += Variable(axes=(station,), data=strarray, atts=varatts['name'])
    # station province
    # station joined
    # geo locators (lat/lon/alt)
    # start/end dates (month relative to 1979-01)
    self.dataset = dataset
    

## load pre-processed EC station time-series
def loadEC_TS(): 
  ''' Load a monthly time-series of pre-processed EC station data. '''
  return NotImplementedError

## load pre-processed EC station climatology
def loadEC(): 
  ''' Load a pre-processed EC station climatology. '''
  return NotImplementedError
  
## Dataset API

dataset_name # dataset name
root_folder # root folder of the dataset
orig_file_pattern = orig_ts_file # filename pattern: variable name and resolution
ts_file_pattern = tsfile # filename pattern: grid
clim_file_pattern = avgfile # filename pattern: variable name and resolution
data_folder = avgfolder # folder for user data
grid_def = None # no grid here...
LTM_grids = None 
TS_grids = None
grid_res = None
default_grid = None
# functions to access specific datasets
loadLongTermMean = None # climatology provided by publisher
loadTimeSeries = None # time-series data
loadClimatology = None # pre-processed, standardized climatology
loadStationTimeSeries = loadEC_TS # time-series data
loadStationClimatology = loadEC # pre-processed, standardized climatology

if __name__ == '__main__':

#   mode = 'test_station_object'
  mode = 'test_station_reader'
#   mode = 'convert_ASCII'
  
  # do some tests
  if mode == 'test_station_object':  
    
    # initialize station (new way with VarDef)
#     var = PrecipDef(name='precipitation', prefix='dt', atts=varatts['precip'])
#     test = DailyStationRecord(id='250M001', name='MOULD BAY', filename='/data/EC/daily_precip/dt/dt250M001.txt',  
#                               begin_year=1948, begin_mon=1, end_year=2007, end_mon=11, prov='NT', joined=True, 
#                               lat=76.2, lon=-119.3, alt=2, **var.getKWargs())    
    var = TempDef(name='maximum temperature', prefix='dx', atts=varatts['Tmax'])
    test = DailyStationRecord(id='5010640', name='CYPRESS RIVER', filename='/data/EC/daily_temp/dx/dx5010640.txt',
                              begin_year=1949, begin_mon=1, end_year=2012, end_mon=3, prov='MB', joined=False, 
                              lat=49.55, lon=-99.08, alt=374, **var.getKWargs())
#     # old way without VarDef    
#     test = DailyStationRecord(id='250M001', name='MOULD BAY', variable='precipitation', units=u'mm', 
#                               varmin=0, varmax=1e3, begin_year=1948, begin_mon=1, end_year=2007, end_mon=11, 
#                               lat=76.2, lon=-119.3, alt=2, prov='NT', joined=True, missing='-9999.99', flags='TEFACLXYZ',
#                               filename='/data/EC/daily_precip/dt/dt250M001.txt', dtype='float32', encoding='UTF-8')
#     test = DailyStationRecord(id='5010640', name='CYPRESS RIVER', variable='maximum temperature', units=u'°C', 
#                               varmin=-100, varmax=100, begin_year=1949, begin_mon=1, end_year=2012, end_mon=3, 
#                               lat=49.55, lon=-99.08, alt=374, prov='MB', joined=False, missing='-9999.9', flags='Ea',
#                               filename='/data/EC/daily_temp/dx/dx5010640.txt', dtype='float32', encoding='ISO-8859-15')
    test.checkHeader() # fail early...
    data = var.convert(test.parseRecord())    
    print data.shape, data.dtype
    print np.nanmin(data), np.nanmean(data), np.nanmax(data)
  
  
  # do some tests
  elif mode == 'test_station_reader':
    
    # prepare input
    variables = dict(Tmax=TempDef(name='maximum temperature', prefix='dx', atts=varatts['Tmax']))
    # initialize station record container
    test = StationRecords(folder='', variables=variables, constraints=dict(prov=('PE',)))
    # show dataset
    test.prepareDataset()
    print test.dataset