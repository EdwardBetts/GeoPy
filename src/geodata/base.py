'''
Created on 2013-08-19

Variable and Dataset classes for handling geographical datasets.

@author: Andre R. Erler, GPL v3
'''

# numpy imports
import numpy as np
import numpy.ma as ma # masked arrays
# my own imports
from atmdyn.properties import variablePlotatts # import plot properties from different file
from misc import VariableError, DatasetError, checkIndex, isFloat, AttrDict

import numbers
import functools
class UnaryCheck(object):
  ''' Decorator class that implements some sanity checks for unary arithmetic operations. '''
  def __init__(self, op):
    ''' Save original operation. '''
    self.op = op
  def __call__(self, orig, arg):
    ''' Perform sanity checks, then execute operation, and return result. '''
    if isinstance(arg,np.ndarray): 
      assert orig.shape == arg.shape, 'Arrays need to have the same shape!' 
      assert orig.dtype == arg.dtype, 'Arrays need to have the same type!'
    else: assert isinstance(arg, numbers.Number), 'Can only operate with numerical types!'
    if not orig.data: orig.load()
    var = self.op(orig,arg)
    assert isinstance(var,Variable)
    return var # return function result
  def __get__(self, instance, klass):
    ''' Support instance methods. This is necessary, so that this class can be bound to the parent instance. '''
    # N.B.: similar implementation to 'partial': need to return a callable that behaves like the instance method
    # def f(arg):
    #  return self.__call__(instance, arg)
    # return f    
    return functools.partial(self.__call__, instance) # but using 'partial' is simpler

def BinaryCheckAndCreateVar(sameUnits=True):
  ''' A decorator function that accepts arguments and returns a decorator class with fixed parameter values. '''
  class BinaryCheckAndCreateVar_Class(object):
    ''' A decorator to perform similarity checks before binary operations and create a new variable instance 
      afterwards; name and units are modified; only non-conflicting attributes are kept. '''
    def __init__(self, binOp):
      ''' Save original operation and parameters. '''
      self.binOp = binOp
      self.sameUnits = sameUnits # passed to constructor function
    # define method wrapper (this is now the actual decorator)
    def __call__(self, orig, other):
      ''' Perform sanity checks, then execute operation, and return result. '''
      assert isinstance(other,Variable), 'Can only add two \'Variable\' instances!' 
      if self.sameUnits: assert orig.units == other.units, 'Variable units have to be identical for addition!'
      assert orig.shape == other.shape, 'Variables need to have the same shape and compatible axes!'
      if not orig.data: orig.load()
      if not other.data: other.load()
      for lax,rax in zip(orig.axes,other.axes):
        assert (lax.coord == rax.coord).all(), 'Variables need to have identical coordinate arrays!'
      # call original method
      data, name, units = self.binOp(orig, other)
      # construct common dict of attributes
      tmp = orig.atts.copy(); tmp.update(other.atts)
      atts = {key:value for key,value in tmp.iteritems() if value == orig.atts[key]}
      atts['name'] = name; atts['units'] = units
      # assign axes (copy from orig)
      axes = [ax for ax in orig.axes]
      var = Variable(name=name, units=units, axes=axes, data=data, atts=atts)
      return var # return new variable instance
    def __get__(self, instance, klass):
      ''' Support instance methods. This is necessary, so that this class can be bound to the parent instance. '''
      return functools.partial(self.__call__, instance)
  # return decorator class  
  return BinaryCheckAndCreateVar_Class


# def BinaryCheckAndCreateVar(sameUnits=True):
#   ''' A decorator to perform similarity checks before binary operations and create a new variable instance 
#       afterwards; name and units are modified; only non-conflicting attributes are kept. '''
#   # first decorator, that processes arguments 
#   # N.B.: basically the first wrapper produces a decorator function with fixed parameter values
#   def decorator_wrapper(binOp):
#     # define method wrapper (this is now the actual decorator)
#     def function_wrapper(self, other):
#       # initial sanity checks
#       assert isinstance(other,Variable), 'Can only add two \'Variable\' instances!' 
#       if sameUnits: assert self.units == other.units, 'Variable units have to be identical for addition!'
#       assert self.shape == other.shape, 'Variables need to have the same shape and compatible axes!'
#       if not self.data: self.load()
#       if not other.data: other.load()
#       for lax,rax in zip(self.axes,other.axes):
#         assert (lax.coord == rax.coord).all(), 'Variables need to have identical coordinate arrays!'
#       # call original method
#       data, name, units = binOp(self, other)
#       # construct common dict of attributes
#       tmp = self.atts.copy(); tmp.update(other.atts)
#       atts = {key:value for key,value in tmp.iteritems() if value == self.atts[key]}
#       atts['name'] = name; atts['units'] = units
#       # assign axes (copy from self)
#       axes = [ax for ax in self.axes]
#       var = Variable(name=name, units=units, axes=axes, data=data, atts=atts)
#       return var # return new variable instance
#     # return wrapper
#     return function_wrapper
#   return decorator_wrapper


## Variable class and derivatives 

class Variable(object):
  ''' 
    The basic variable class; it mainly implements arithmetic operations and indexing/slicing.
  '''
  
  def __init__(self, name='N/A', units='N/A', axes=None, data=None, mask=None, fillValue=None, atts=None, plotatts=None):
    ''' 
      Initialize variable and attributes.
      
      Basic Attributes:
        name = '' # short name, e.g. used in datasets
        units = '' # physical units
        data = False # logical indicating whether a data array is present/loaded 
        axes = None # a tuple of references to coordinate variables (also Variable instances)
        data_array = None # actual data array (None if not loaded)
        shape = None # length of dimensions, like an array
        ndim = None # number of dimensions
        dtype = '' # data type (string)
        
      Optional/Advanced Attributes:
        masked = False # whether or not the array in self.data is a masked array
        fillValue = None # value to fill in for masked values
        atts = None # dictionary with additional attributes
        plotatts = None # attributed used for displaying the data       
    '''
    # basic input check
    if data is None:
      ldata = False; shape = None; dtype = ''
    else:
      assert isinstance(data,np.ndarray), 'The data argument must be a numpy array!'
      ldata = True; shape = data.shape; dtype = data.dtype
      if axes is not None:
        assert len(axes) == data.ndim, 'Dimensions of data array and axes are note compatible!'
    # for completeness of MRO...
    super(Variable,self).__init__()
    # set basic variable 
    self.__dict__['name'] = name
    self.__dict__['units'] = units
    # set defaults - make all of them instance variables! (atts and plotatts are set below)
    self.__dict__['data_array'] = None
    self.__dict__['data'] = ldata
    self.__dict__['shape'] = shape
    self.__dict__['dtype'] = dtype
    self.__dict__['masked'] = False # handled in self.load() method    
    # figure out axes
    if axes is not None:
      assert isinstance(axes, (list, tuple))
      if all([isinstance(ax,Axis) for ax in axes]):
        if ldata: 
          for ax,n in zip(axes,shape): ax.updateLength(n)
        if not ldata and all([len(ax) for ax in axes]):
          self.__dict__['shape'] = [len(ax) for ax in axes] # get shape from axes
      elif all([isinstance(ax,basestring) for ax in axes]):
        if ldata: axes = [Axis(name=ax, len=n) for ax,n in zip(axes,shape)] # use shape from data
        else: axes = [Axis(name=ax) for ax in axes] # initialize without shape
    else: 
      raise VariableError, 'Cannot initialize %s instance \'%s\': no axes declared'%(self.var.__class__.__name__,self.name)
    self.__dict__['axes'] = tuple(axes) 
    self.__dict__['ndim'] = len(axes)  
    # create shortcuts to axes (using names as member attributes) 
    for ax in axes: self.__dict__[ax.name] = ax
    # cast attributes dicts as AttrDict to fcilitate easy access 
    if atts is None: atts = dict(name=self.name, units=self.units)
    self.__dict__['atts'] = AttrDict(**atts)
    if plotatts is None: # try to find sensible default values 
      if variablePlotatts.has_key(self.name): plotatts = variablePlotatts[self.name]
      else: plotatts = dict(plotname=self.name, plotunits=self.units, plottitle=self.name) 
    self.__dict__['plot'] = AttrDict(**plotatts)
    # guess fillValue
    if fillValue is None:
      if 'fillValue' in atts: fillValue = atts['fillValue']
      elif '_fillValue' in atts: fillValue = atts['_fillValue']
      else: fillValue = None
    self.__dict__['fillValue'] = fillValue
    # assign data, if present (can initialize without data)
    if data is not None: 
      self.load(data, mask=mask, fillValue=fillValue) # member method defined below
    
    
#   def __getattr__(self, name):
#     ''' Return contents of atts or plotatts dictionaries as if they were attributes. '''
#     # N.B.: before this method is called, instance attributes are checked automatically
#     if self.__dict__.has_key(name): # check instance attributes first
#       return self.__dict__[name]
#     elif self.__dict__['atts'].has_key(name): # try atts second
#       return self.__dict__['atts'][name] 
#     elif self.__dict__['plotatts'].has_key(name): # then try plotatts
#       return self.__dict__['plotatts'][name]
#     else: # or throw attribute error
#       raise AttributeError, '\'%s\' object has no attribute \'%s\''%(self.__class__.__name__,name)
    
#   def __setattr__(self, name, value):
#     ''' Change the value of class existing class attributes, atts, or plotatts entries,
#       or store a new attribute in the 'atts' dictionary. '''
#     if self.__dict__.has_key(name): # class attributes come first
#       self.__dict__[name] = value # need to use __dict__ to prevent recursive function call
#     elif self.__dict__['atts'].has_key(name): # try atts second
#       self.__dict__['atts'][name] = value    
#     elif self.__dict__['plotatts'].has_key(name): # then try plotatts
#       self.__dict__['plotatts'][name] = value
#     else: # if the attribute does not exist yet, add it to atts or plotatts
#       if name[0:4] == 'plot':
#         self.plotatts[name] = value
#       else:
#         self.atts[name] = value
  
  def hasAxis(self, axis):
    ''' Check if the variable instance has a particular axis. '''
    if isinstance(axis,basestring): # by name
      for i in xrange(len(self.axes)):
        if self.axes[i].name == axis: return True
    elif isinstance(axis,Variable): # by object ID
      for i in xrange(len(self.axes)):
        if self.axes[i] == axis: return True
    # if all fails
    return False

  def __contains__(self, axis):
    ''' Check if the variable instance has a particular axis. '''
    # same as class method
    return self.hasAxis(axis)
  
  def __len__(self):
    ''' Return number of dimensions. '''
    return self.__dict__['ndim']
  
  def axisIndex(self, axis):
    ''' Return the index of a particular axis. (return None if not found) '''
    if isinstance(axis,basestring): # by name
      for i in xrange(len(self.axes)):
        if self.axes[i].name == axis: return i
    elif isinstance(axis,Variable): # by object ID
      for i in xrange(len(self.axes)):
        if self.axes[i] == axis: return i
    # if all fails
    return None
        
  def __getitem__(self, idx):
    ''' Method implementing access to the actual data, plus some extras. '''          
    # determine what to do
    if all(checkIndex(idx, floatOK=True)):      
      # array indexing: return array slice
      if self.data:
        if any(isFloat(idx)): raise NotImplementedError, \
          'Floating-point indexing is not implemented yet for \'%s\' class.'%(self.__class__.__name__)
        return self.data_array.__getitem__(idx) # valid array slicing
      else: 
        raise IndexError, 'Variable instance \'%s\' has no associated data array!'%(self.name) 
    elif isinstance(idx,basestring) or isinstance(idx,Axis):
      # dictionary-type key: return index of dimension with that name
      return self.axisIndex(idx)
    else:    
      # if nothing applies, raise index error
      raise IndexError, 'Invalid index/key type for class \'%s\'!'%(self.__class__.__name__)

  def load(self, data=None, mask=None, fillValue=None):
    ''' Method to attach numpy data array to variable instance (also used in constructor). '''
    assert data is not None, 'A basic \'Variable\' instance requires external data to load!'
    assert isinstance(data,np.ndarray), 'The data argument must be a numpy array!'          
    if mask: 
      self.__dict__['data_array'] = ma.array(data, mask=mask)
    else: 
      self.__dict__['data_array'] = data
    if isinstance(self.data_array, ma.MaskedArray): 
      self.__dict__['masked'] = True # set masked flag
    self.__dict__['data'] = True
    self.__dict__['shape'] = data.shape
    assert len(self.shape) == self.ndim, 'Variable dimensions and data dimensions incompatible!'
    self.__dict__['dtype'] = data.dtype
    if self.masked: # figure out fill value for masked array
      if fillValue is None: self.__dict__['fillValue'] = ma.default_fill_value(data)
      else: self.__dict__['fillValue'] = fillValue
    # some more checks
    # N.B.: Axis objects carry a circular reference to themselves in the dimensions tuple; hence
    #       the coordinate vector has to be assigned before the dimensions size can be checked 
    assert len(self.axes) == len(self.shape), 'Dimensions of data array and variable must be identical!'
    for ax,n in zip(self.axes,self.shape): 
      ax.updateLength(n) # update length is all we can do without a coordinate vector       
     
  def unload(self):
    ''' Method to unlink data array. '''
    self.__dict__['data_array'] = None # unlink data array
    self.__dict__['data'] = False # set data flag
    self.__dict__['fillValue'] = None
    # self.__dict__['shape'] = None # retain shape for later use
    
  def getArray(self, idx=None, axes=None, broadcast=False, unmask=True, fillValue=None, copy=True):
    ''' Copy the entire data array or a slice; option to unmask and to reorder/reshape to specified axes. '''
    # get data
    if all(checkIndex(idx, floatOK=True)):
      if not self.data: self.load(data=idx) 
      if copy: datacopy = self.__getitem__(idx).copy() # use __getitem__ to get slice
      else: datacopy = self.__getitem__(idx) # just get a view
    else:
      if not self.data: self.load() 
      if copy: datacopy = self.data_array.copy() # copy entire array
      else: datacopy = self.data_array
    # unmask    
    if unmask and self.masked:
      if fillValue is None: fillValue=self.fillValue
      datacopy = datacopy.filled(fill_value=fillValue) # I don't know if this generates a copy or not...
    # reorder and reshape to match axes (add missing dimensions as singleton dimensions)
    if axes is not None:
      if idx is not None: raise NotImplementedError
      for ax in self.axes:
        assert (ax in axes) or (ax.name in axes), "Can not broadcast Variable '%s' to dimension '%s' "%(self.name,ax.name)
      # order dimensions as in broadcast axes list
      order = [self.axisIndex(ax) for ax in axes if self.hasAxis(ax)] # indices of broadcast list axes in instance axes list (self.axes)
      datacopy = np.transpose(datacopy,axes=order) # reorder dimensions to match broadcast list
      # adapt shape for broadcasting (i.e. expand shape with singleton dimensions)
      shape = [1]*len(axes); z = 0
      for i in xrange(len(axes)):
        if self.hasAxis(axes[i]): 
          shape[i] = datacopy.shape[z] # indices of instance axes in broadcast axes list
          z += 1
      assert z == datacopy.ndim 
      datacopy = datacopy.reshape(shape)
    # true broadcasting: extend array to match given axes and dimensions
    if broadcast:
      assert all([isinstance(ax,Axis) and len(ax)>0 for ax in axes]),\
         'All axes need to have a defined length in order broadcast the array.'
      # get tiling list
      tiling = [len(ax) if l == 1 else 1 for ax,l in zip(axes,datacopy.shape)]
      datacopy = np.tile(datacopy, reps=tiling)
    # return array
    return datacopy
    
  def mask(self, mask=None, fillValue=None, merge=True):
    ''' A method to add a mask to an unmasked array, or extend or replace an existing mask. '''
    if mask is not None:
      assert isinstance(mask,np.ndarray) or isinstance(mask,Variable), 'Mask has to be a numpy array or a Variable instance!'
      # 'mask' can be a variable
      if isinstance(mask,Variable):
        mask = (mask.getArray(unmask=True,axes=self.axes) > 0) # convert to a boolean numpy array
#         for ax in mask.axes: 
#           assert ax in self, "Variable '%s' does not have mask '%s' dimension '%s' "%(self.name,mask.name,ax.name)
#         axidx = [self.axisIndex(ax) for ax in mask.axes] # get indices in this variable array
#         mask = (mask.getArray(unmask=True) > 0) # convert to a boolean numpy array
#         # order dimensions as in this variable
#         mask = np.transpose(mask,axes=np.argsort(axidx))
#         axidx.sort() # also index list
#         # adapt shape for broadcasting
#         shape = []; oi = 0
#         for i in xrange(len(self)):
#           if i == oi: 
#             shape.append(mask.shape[oi])
#             oi += 1
#           else: shape.append(1) # a size of '1' will be expanded by broadcast later
#         assert oi == mask.ndim
#         mask = mask.reshape(shape)        
#         # check order of dimensions
#         if mask.ndim == 1: pass
#         elif mask.ndim == 2:
#           if axidx[0] == axidx[1]+1: 
#             mask = mask.swapaxes(0,1)  
#             axidx.reverse()  
#           elif axidx[0]+1 == axidx[1]: pass
#           else:
#             raise NotImplementedError
#         else:
#           order = range(axidx[0], axidx[0]+len(axidx))
#           assert order == axidx, "Incompatible axis order that can not be resolved."
#         # extend mask to the right, if necessary
#         if axidx[-1] < len(self)-1:
#           rext = self.shape[axidx[-1]+1:]
#           mask.reshape(mask.shape+(1,)*len(rext))
#           mask.tile((1,)*len(axidx)+rext)
#       else:
#         # or a numbpy array
#         assert isinstance(mask,np.ndarray)
#         axidx = range(len(self)-mask.ndim,len(self)) # assume it is the innermost dimensions      
#         for i in xrange(len(axidx)):
#           assert self.shape[axidx[i]] == mask.shape[i], "Shape of mask and Variable are incompatible!"   
#       # expand array to the left, if necessary
#       if axidx[0] > 0:
#           lext = self.shape[0:axidx[0]]
#           mask.reshape((1,)*len(lext)+mask.shape)
#           mask.tile(lext+(1,)*len(axidx))
      assert isinstance(mask,np.ndarray), 'Mask has to be convertible to a numpy array!'      
      # if 'mask' has less dimensions than the variable, it can be extended      
      assert len(self.shape) >= len(mask.shape), 'Data array needs to have the same number of dimensions or more than the mask!'
      assert self.shape[self.ndim-mask.ndim:] == mask.shape, 'Data array and mask have to be of the same shape!'
      # broadcast mask to data array
      mask = np.broadcast_arrays(mask,self.data_array)[0] # only need first element (the broadcasted mask)
      # create new data array
      if merge and self.masked: # the first mask is usually the land-sea mask, which we want to keep
        data = self.getArray(unmask=False) # get data with mask
        mask = ma.mask_or(data.mask, mask, copy=True, shrink=False) # merge masks
      else: 
        data = self.getArray(unmask=True) # get data without mask
      self.__dict__['data_array'] = ma.array(data, mask=mask)
      # change meta data
      self.__dict__['masked'] = True
      if fillValue: 
        self.data_array.set_fill_value(fillValue)
        self.__dict__['fillValue'] = fillValue
      else:  
        self.__dict__['fillValue'] = self.data_array.get_fill_value() # probably just the default
    
  def unmask(self, fillValue=None):
    ''' A method to remove and existing mask and fill the gaps with fillValue. '''
    if self.masked:
      if fillValue is None: fillValue = self.fillValue # default
      self.__dict__['data_array'] = self.data_array.filled(fill_value=fillValue)
      # change meta data
      self.__dict__['masked'] = False
      self.__dict__['fillValue'] = None  
    
  def getMask(self, nomask=False):
    ''' Get the mask of a masked array or return a boolean array of False (no mask). '''
    if nomask: return ma.getmask(self.data_array)
    else: return ma.getmaskarray(self.data_array)    

  @UnaryCheck    
  def __iadd__(self, a):
    ''' Add a number or an array to the existing data. '''      
    self.data_array += a    
    return self # return self as result

  @UnaryCheck
  def __isub__(self, a):
    ''' Subtract a number or an array from the existing data. '''      
    self.data_array -= a
    return self # return self as result
  
  @UnaryCheck
  def __imul__(self, a):
    ''' Multiply the existing data with a number or an array. '''      
    self.data_array *= a
    return self # return self as result

  @UnaryCheck
  def __idiv__(self, a):
    ''' Divide the existing data by a number or an array. '''      
    self.data_array /= a
    return self # return self as result
  
  @BinaryCheckAndCreateVar(sameUnits=True)
  def __add__(self, other):
    ''' Add two variables and return a new variable. '''
    data = self.data_array + other.data_array
    name = '%s + %s'%(self.name,other.name)
    units = self.units
    return data, name, units

  @BinaryCheckAndCreateVar(sameUnits=True)
  def __sub__(self, other):
    ''' Subtract two variables and return a new variable. '''
    data = self.data_array - other.data_array
    name = '%s - %s'%(self.name,other.name)
    units = self.units
    return data, name, units
  
  @BinaryCheckAndCreateVar(sameUnits=False)
  def __mul__(self, other):
    ''' Multiply two variables and return a new variable. '''
    data = self.data_array * other.data_array
    name = '%s x %s'%(self.name,other.name)
    units = '%s %s'%(self.units,other.units)
    return data, name, units

  @BinaryCheckAndCreateVar(sameUnits=False)
  def __div__(self, other):
    ''' Divide two variables and return a new variable. '''
    data = self.data_array / other.data_array
    name = '%s / %s'%(self.name,other.name)
    units = '%s / (%s)'%(self.units,other.units)
    return data, name, units


class Axis(Variable):
  '''
    A special class of 1-dimensional variables for coordinate variables.
     
    It is essential that this class does not overload any class methods of Variable, 
    so that new Axis sub-classes can be derived from new Variable sub-classes via 
    multiple inheritance from the Variable sub-class and this class. 
  '''
  
  coord = None # the coordinate vector (also accessible as data_array)
  len = 0 # the length of the dimension (integer value)
  
  def __init__(self, length=0, coord=None, **varargs):
    ''' Initialize a coordinate axis with appropriate values. '''
    # initialize dimensions
    axes = (self,)
    # N.B.: Axis objects carry a circular reference to themselves in the dimensions tuple
    self.__dict__['coord'] = None
    self.__dict__['len'] = length 
    # initialize as a subclass of Variable, depending on the multiple inheritance chain
    super(Axis, self).__init__(axes=axes, **varargs)
    # add coordinate vector
    if coord is not None: self.updateCoord(coord)
    elif length > 0: self.updateLength(length)
    
  def load(self, *args, **kwargs):
    ''' Load a coordinate vector into an axis and update related attributes. '''
    # load data
    super(Axis,self).load(*args, **kwargs) # call load of base variable (Variable subclass)
    # update attributes
    self.__dict__['coord'] = self.data_array
    self.__dict__['len'] = self.data_array.shape[0]
    
  def unload(self):
    ''' Remove the coordinate vector of an axis but keep length attribute. '''
    # load data
    super(Axis,self).unload() # call unload of base variable (Variable subclass)
    # update attributes
    self.__dict__['coord'] = None
#     self.__dict__['len'] = 0
    
  def updateCoord(self, coord=None, **varargs):
    ''' Update the coordinate vector of an axis based on certain conventions. '''
    # resolve coordinates
    if coord is None:
      # this means the coordinate vector/data is going to be deleted 
      self.unload()
    else:
      # a coordinate vector will be created and loaded, based on input conventions
      if isinstance(coord,tuple) and ( 0 < len(coord) < 4):
        data = np.linspace(*coord)
      elif isinstance(coord,np.ndarray) and coord.ndim == 1:
        data = coord
      elif isinstance(coord,tuple) or isinstance(coord,list):
        data = np.asarray(coord)
      else: #data = coord
        raise TypeError, 'Data type not supported for coordinate values.'
      # load data
      self.load(data, mask=None, **varargs)
      

  def __len__(self):
    ''' Return length of dimension. '''
    return self.__dict__['len'] 
    
  def updateLength(self, length=0):
    ''' Update the length, or check for conflict if a coordinate vector is present. (Default is length=0)'''
    if self.data:
      assert length == self.shape[0], \
        'Coordinate vector of Axis instance \'%s\' is incompatible with given length: %i != %i'%(self.name,len(self),length)
    else:
      self.__dict__['len'] = length
      

class Dataset(object):
  '''
    A container class for variable and axes objects, as well as some meta information. This class also 
    implements collective operations on all variables in the dataset.
  '''
  
  def __init__(self, varlist=None, atts=None):
    ''' 
      Create a dataset from a list of variables. The basic dataset class has no capability to create variables.
      
      Basic Attributes:
        variables = dict() # dictionary holding Variable instances
        axes = dict() # dictionary holding Axis instances (inferred from Variables)
        atts = AttrDict() # dictionary containing global attributes / meta data
    '''
    # create instance attributes
    self.__dict__['variables'] = dict()
    self.__dict__['axes'] = dict()
    # load global attributes, if given
    if atts: self.__dict__['atts'] = AttrDict(**atts)
    else: self.__dict__['atts'] = AttrDict()
    # load variables (automatically adds axes linked to varaibles)
    for var in varlist:
      #print var.name
      self.addVariable(var)
    
  def addAxis(self, ax):
    ''' Method to add an Axis to the Dataset. If the Axis is already present, check that it is the same. '''
    assert isinstance(ax,Axis)
    if ax.name not in self.axes: # add new axis, if it does not already exist        
      assert ax.name not in self.__dict__, "Cannot add Axis '%s' to Dataset, because an attribute of the same name already exits!"%(ax.name) 
      self.axes[ax.name] = ax
      self.__dict__[ax.name] = self.axes[ax.name] # create shortcut
    else: # make sure the axes are consistent between variable (i.e. same name, same axis)
      assert ax is self.axes[ax.name], "Error: Axis '%s' in Variable '%s' and Dataset are different!"%(ax.name,var.name)
    # double-check
    return self.axes.has_key(ax.name)       
    
  def addVariable(self, var):
    ''' Method to add a Variable to the Dataset. If the variable is already present, abort. '''
    assert isinstance(var,Variable)
    assert var.name not in self.__dict__, "Cannot add Variable '%s' to Dataset, because an attribute of the same name already exits!"%(var.name)
    # add axes, if necessary (or check, if already present)
    for ax in var.axes: self.addAxis(ax) # implemented slightly differently
    # finally, if everything is OK, add variable
    self.variables[var.name] = var
    self.__dict__[var.name] = self.variables[var.name] # create shortcut
    # double-check
    return self.variables.has_key(var.name) 
    
  def removeAxis(self, ax):
      ''' Method to remove an Axis from the Dataset, provided it is no longer needed. '''
      if isinstance(ax,basestring): ax = self.axes[ax] # only work with Axis objects
      assert isinstance(ax,Axis), "Argument 'ax' has to be an Axis instance or a string representing the name of an axis." 
      if ax.name in self.axes: # remove axis, if it does exist
        # make sure no variable still needs axis
        if not any([var.hasAxis(ax) for var in self.variables.itervalues()]):
          # delete axis from dataset   
          del self.axes[ax.name]
          del self.__dict__[ax.name]
          return True
        # don't delete, if still needed
      # double-check (return True, if axis is not present, False, if it is)
      return not self.axes.has_key(ax.name)       
  
  def removeVariable(self, var):
    ''' Method to remove a Variable from the Dataset. '''
    if isinstance(var,basestring): var = self.variable[var] # only work with Variable objects
    assert isinstance(var,Variable), "Argument 'var' has to be a Variable instance or a string representing the name of a variable."
    if var.name in self.variables: # add new variable if it does not already exist
      # delete variable from dataset   
      del self.variables[var.name]
      del self.__dict__[var.name]
    # double-check (return True, if variable is not present, False, if it is)
    return not self.variables.has_key(var.name)
  
  def hasVariable(self, var):
    ''' Method to check, if a Variable is present in the Dataset. '''
    if isinstance(var,basestring):
      return self.variables.has_key(var) # look up by name
    elif isinstance(var,Variable):
      if self.variables.has_key(var.name):
        assert self.variables[var.name] is var, "The Dataset contains a different Variable of the same name!"
        return True # name found and identity verified 
      else: return False # not found
    else: # invalid input
      raise DatasetError, "Need a Variable instance or name to check for a Variable in the Dataset!"
  
  def hasAxis(self, ax):
    ''' Method to check, if an Axis is present in the Dataset. '''
    if isinstance(ax,basestring):
      return self.axes.has_key(ax) # look up by name
    elif isinstance(ax,Axis):
      if self.axes.has_key(ax.name):
        assert self.axes[ax.name] is ax, "The Dataset contains a different Variable of the same name!"
        return True # name found and identity verified 
      else: return False # not found
    else: # invalid input
      raise DatasetError, "Need a Axis instance or name to check for an Axis in the Dataset!"
    
  def __contains__(self, var):
    ''' Check if the Dataset instance has a particular Variable or Axis. '''
    # variable or axis
    return self.hasVariable(var) or self.hasAxis(var)
  
  def __len__(self):
    ''' Get the number of Variables in the Dataset. '''
    return len(self.variables)
    
  def __iadd__(self, var):
    ''' Add a Variable to an existing dataset. '''      
    assert self.addVariable(var), "A proble occurred adding Variable '%s' to Dataset."%(var.name)    
    return self # return self as result

  def __isub__(self, var):
    ''' Remove a Variable to an existing dataset. '''      
    assert self.removeVariable(var), "A proble occurred removing Variable '%s' from Dataset."%(var.name)
    return self # return self as result
  
  def load(self, data=None, mask=None, fillValue=None, **kwargs):
    ''' Issue load() command to all variable; pass on any keyword arguments. '''
    for var in self.variables.itervalues():
      var.load(data=data, mask=mask, fillValue=fillValue, **kwargs)
      
  def unload(self, **kwargs):
    ''' Unload all data arrays currently loaded in memory. '''
    for var in self.variables.itervalues():
      var.unload(**kwargs)
      
  def mask(self, mask=None, **kwargs):
    ''' Apply 'mask' to all variables and add the mask, if it is a variable. '''
    if isinstance(mask,Variable) and not self.hasVariable(mask): self.addVariable(mask)
    for var in self.variables.itervalues():
      if var.ndim >= mask.ndim: var.load(mask=mask, **kwargs)
    
  def unmask(self, fillValue=None, **kwargs):
    ''' Unmask all Variables in the Dataset. '''
    for var in self.variables.itervalues():
      var.load(fillValue=fillValue, **kwargs)
        

## run a test    
if __name__ == '__main__':

  # initialize test objects
  x = Axis(name='x', units='none', coord=(1,5,5))
  y = Axis(name='y', units='none', coord=(1,5,5))
  var = Variable(name='test',units='none',axes=(x,y),data=np.zeros((5,5)),atts=dict(_FillValue=-9999))
  
  # variable test
  print
  var += 1
  # test getattr
  print 'Name: %s, Units: %s, Missing Values: %s'%(var.name, var.units, var._FillValue)
  # test setattr
  var.Comments = 'test'; var.plotComments = 'test' 
  print 'Comments: %s, Plot Comments: %s'%(var.Comments,var.plotatts['plotComments'])
#   print var[:]
  # indexing (getitem) test
  print var.shape, var[2,2:5:2]
  var.unload()
#   print var.data
  
  # axis test
  print 
  # test contains 
  print var[x]
  for ax in (x,y):
    if ax in var: print '%s is the %i. axis and has length %i'%(ax.name,var[ax]+1,len(ax))
