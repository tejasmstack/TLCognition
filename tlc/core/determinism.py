"""Determinism environment — spec 03 §7.2.2. Import BEFORE numpy in every entry point.

Multi-threaded BLAS changes summation order, which changes the last ULP, which can flip a
threshold comparison and change a spot count. Single-threaded BLAS is mandatory, not tunable.
"""

import os

for _v in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
    os.environ[_v] = "1"
os.environ["PYTHONHASHSEED"] = "0"
