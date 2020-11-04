import os
from datetime import datetime

import scipy.sparse as sps
import numpy as np
import xarray as xr


def latlon_to_scrip(nx, ny, lon0=-180., grid_imask=None, file_out=None):
    """Generate a SCRIP grid file for a regular lat x lon grid.
    
    Parameters
    ----------
    
    nx : int
       Number of points in x (longitude).
    ny : int
       Number of points in y (latitude).
    lon0 : float, optional [default=-180]
       Longitude on lefthand grid boundary.
    grid_imask : array-like, optional [default=None]       
       If the value is set to 0 for a grid point, then that point is
       considered masked out and won't be used in the weights 
       generated by the application. 
    file_out : string, optional [default=None]
       File to which to write the grid.

    Returns
    -------
    
    ds : xarray.Dataset
       The grid file dataset.       
    """
    
    # compute coordinates of regular grid
    dx = 360. / nx
    dy = 180. / ny
    lat = np.arange(-90. + dy / 2., 90., dy)
    lon = np.arange(lon0 + dx / 2., lon0 + 360., dx)

    # make 2D
    y_center = np.broadcast_to(lat[:, None], (ny, nx))
    x_center = np.broadcast_to(lon[None, :], (ny, nx))

    # compute corner points: must be counterclockwise
    y_corner = np.stack((y_center - dy / 2.,  # SW
                         y_center - dy / 2.,  # SE
                         y_center + dy / 2.,  # NE
                         y_center + dy / 2.), # NW
                        axis=2)

    x_corner = np.stack((x_center - dx / 2.,  # SW
                         x_center + dx / 2.,  # SE
                         x_center + dx / 2.,  # NE
                         x_center - dx / 2.), # NW
                        axis=2)

    # compute area
    y0 = np.sin(y_corner[:, :, 0] * np.pi / 180.) # south
    y1 = np.sin(y_corner[:, :, 3] * np.pi / 180.) # north
    x0 = x_corner[:, :, 0] * np.pi / 180.         # west
    x1 = x_corner[:, :, 1] * np.pi / 180.         # east
    grid_area = (y1 - y0) * (x1 - x0)
    
    # sum of area should be equal to area of sphere
    np.testing.assert_allclose(grid_area.sum(), 4.*np.pi)
    
    # construct mask
    if grid_imask is None:
        grid_imask = np.ones((ny, nx), dtype=np.int32)
    
    # generate output dataset
    dso = xr.Dataset()    
    dso['grid_dims'] = xr.DataArray(np.array([nx, ny], dtype=np.int32), 
                                    dims=('grid_rank',)) 
    dso.grid_dims.encoding = {'dtype': np.int32}

    dso['grid_center_lat'] = xr.DataArray(y_center.reshape((-1,)), 
                                          dims=('grid_size'),
                                          attrs={'units': 'degrees'})

    dso['grid_center_lon'] = xr.DataArray(x_center.reshape((-1,)), 
                                          dims=('grid_size'),
                                          attrs={'units': 'degrees'})
    
    dso['grid_corner_lat'] = xr.DataArray(y_corner.reshape((-1, 4)), 
                                          dims=('grid_size', 'grid_corners'), 
                                          attrs={'units': 'degrees'})
    dso['grid_corner_lon'] = xr.DataArray(x_corner.reshape((-1, 4)), 
                                      dims=('grid_size', 'grid_corners'), 
                                      attrs={'units': 'degrees'})    

    dso['grid_imask'] = xr.DataArray(grid_imask.reshape((-1,)), 
                                     dims=('grid_size'),
                                     attrs={'units': 'unitless'})
    dso.grid_imask.encoding = {'dtype': np.int32}
    
    dso['grid_area'] = xr.DataArray(grid_area.reshape((-1,)), 
                                     dims=('grid_size'),
                                     attrs={'units': 'radians^2',
                                            'long_name': 'area weights'})
    
    # force no '_FillValue' if not specified
    for v in dso.variables:
        if '_FillValue' not in dso[v].encoding:
            dso[v].encoding['_FillValue'] = None

    dso.attrs = {'title': f'{dy} x {dx} (lat x lon) grid',
                 'created_by': 'latlon_to_scrip',
                 'date_created': f'{datetime.now()}',
                 'conventions': 'SCRIP',
                }
            
    # write output file
    if file_out is not None:
        print(f'writing {file_out}')
        dso.to_netcdf(file_out)
        
    return dso


def esmf_apply_weights(weights, indata, shape_in, shape_out):
        '''
        Apply regridding weights to data.
        Parameters
        ----------
        A : scipy sparse COO matrix
        indata : numpy array of shape ``(..., n_lat, n_lon)`` or ``(..., n_y, n_x)``.
            Should be C-ordered. Will be then tranposed to F-ordered.
        shape_in, shape_out : tuple of two integers
            Input/output data shape for unflatten operation.
            For rectilinear grid, it is just ``(n_lat, n_lon)``.
        Returns
        -------
        outdata : numpy array of shape ``(..., shape_out[0], shape_out[1])``.
            Extra dimensions are the same as `indata`.
            If input data is C-ordered, output will also be C-ordered.
        '''



        # COO matrix is fast with F-ordered array but slow with C-array, so we
        # take in a C-ordered and then transpose)
        # (CSR or CRS matrix is fast with C-ordered array but slow with F-array)
        if not indata.flags['C_CONTIGUOUS']:
            warnings.warn("Input array is not C_CONTIGUOUS. "
                          "Will affect performance.")

        # get input shape information
        shape_horiz = indata.shape[-2:]
        extra_shape = indata.shape[0:-2]

        assert shape_horiz == shape_in, (
            'The horizontal shape of input data is {}, different from that of'
            'the regridder {}!'.format(shape_horiz, shape_in)
            )

        assert shape_in[0] * shape_in[1] == weights.shape[1], (
            "ny_in * nx_in should equal to weights.shape[1]"
        )

        assert shape_out[0] * shape_out[1] == weights.shape[0], (
            "ny_out * nx_out should equal to weights.shape[0]"
        )

        # use flattened array for dot operation
        indata_flat = indata.reshape(-1, shape_in[0]*shape_in[1])
        outdata_flat = weights.dot(indata_flat.T).T

        # unflattened output array
        outdata = outdata_flat.reshape(
            [*extra_shape, shape_out[0], shape_out[1]])
        return outdata
    
class regridder(object):
    """simple class to enable regridding"""
    
    def __init__(self, src_grid_file, dst_grid_file, weight_file):
        
        # TODO: do I actually need the grid files here?
        #       shouldn't all the information be in the weight file?
        self.src_grid_file = src_grid_file
        self.dst_grid_file = dst_grid_file
        
        with xr.open_dataset(src_grid_file) as src:
            self.dims_src = tuple(src.grid_dims.values[::-1])
    
        with xr.open_dataset(dst_grid_file) as dst:
            self.dims_dst = tuple(dst.grid_dims.values[::-1])
            self.mask_dst = dst.grid_imask.values.reshape(self.dims_dst).T

        n_dst = np.prod(self.dims_dst)
        n_src = np.prod(self.dims_src)
        print(f'source grid dims: {self.dims_src}')
        print(f'destination grid dims: {self.dims_dst}')

        with xr.open_dataset(weight_file) as mf:
            row = mf.row.values - 1
            col = mf.col.values - 1
            S = mf.S.values
        self.weights = sps.coo_matrix((S, (row, col)), shape=[n_dst, n_src])

    def __repr__(self):
        return (
            f'regridder {os.path.basename(self.src_grid_file)} --> {os.path.basename(self.dst_grid_file)}'
        )
    
    def regrid_dataarray(self, da_in, renormalize=True, apply_mask=True):
        """regrid DataArray"""
        # Pull data, dims and coords from incoming DataArray
        data_src = da_in.data
        non_lateral_dims = da_in.dims[:-2]
        copy_coords = {d: da_in.coords[d] for d in non_lateral_dims if d in da_in.coords}

        # If renormalize == True, remap a field of ones
        if renormalize:
            ones_src = np.where(np.isnan(data_src), 0.0, 1.0)
            data_src = np.where(np.isnan(data_src), 0.0, data_src)

        # remap the field
        data_dst = esmf_apply_weights(
            self.weights, data_src, self.dims_src, self.dims_dst
        )

        # Renormalize to include non-missing data_src
        # TODO: it would be nice to include a threshold here,
        #       the user could specify a fraction of mapped points, 
        #       below which the value yields missing in the data_dst
        if renormalize:
            old_err_settings = np.seterr(invalid='ignore')
            ones_dst = esmf_apply_weights(
                self.weights, ones_src, self.dims_src, self.dims_dst
            )
            ones_dst = np.where(ones_dst > 0.0, ones_dst, np.nan)
            data_dst = data_dst / ones_dst
            data_dst = np.where(ones_dst > 0.0, data_dst, np.nan)
            np.seterr(**old_err_settings)

        # reform into xarray.DataArray
        da_out = xr.DataArray(
            data_dst, name=da_in.name, dims=da_in.dims, attrs=da_in.attrs, coords=copy_coords
        )

        # Apply a missing-values mask
        if apply_mask:
            da_out = da_out.where(self.mask_dst.T)

        return da_out
    