# Copepod biomass fields

Read data from https://www.st.nmfs.noaa.gov/copepod/biomass/biomass-fields.html

and convert to netCDF and regrid.

## Conda environment
We are using `conda-lock` to ensure a reproducible environment. Please update the conda-lock files when updating the environment.

To create an environment from the lock file
```bash
conda create -n copepod --file environment/conda-linux-64.lock
```
or
```bash
conda create -n copepod --file environment/conda-osx-64.lock
```