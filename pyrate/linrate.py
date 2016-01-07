"""
Pixel-by-pixel linear rate (velocity) estimation using iterative weighted
least-squares method.

Based on original Matlab code by Hua Wang and Juliet Biggs, and Matlab 'lscov'
function.

.. codeauthor: Matt Garthwaite and Sudipta Basak, GA
"""

from scipy.linalg import solve, cholesky, qr, inv
from numpy import nan, isnan, sqrt, diag, delete, ones, array, nonzero, float32
import numpy as np
import parmap


def is_pos_def(x):
    """
    Can be used to check if matrix x is +ve def.
    Works on the basis that all eigenvalues should be +ve
    :param x:
    :return:
    """
    if x.shape[0] == x.shape[1]:
        return np.all(np.linalg.eigvals(x) > 1e-6)
    else:
        return False


def linear_rate(ifgs, vcm, pthr, nsig, maxsig, mst=None, parallel=True):
    """
    Pixel-by-pixel linear rate (velocity) estimation using iterative weighted least-squares method.

    :param ifgs: Sequence of ifg objs from which to extract observations
    :param vcm: Derived positive definite temporal variance covariance matrix
    :param pthr: Pixel threshold; minimum number of coherent observations for a pixel
    :param nsig: n-sigma ratio used to threshold 'model minus observation' residuals
    :param maxsig: Threshold for maximum allowable standard error
    :param mst: Pixel-wise matrix describing the minimum spanning tree network
    :param parallel: use multiprocessing or not.

    :return:
        python/matlab variable names
        rate/ifg_stack: stacked interferogram (i.e., rate map)
        error/std_stack: standard deviation of the stacked interferogram
                  (i.e., error map)
        samples/coh_sta: statistics of coherent pixels used for stacking
        demerror:  dem errors in metres, no implemented in python
    """

    rows, cols = ifgs[0].phase_data.shape

    # make 3D block of observations
    obs = array([np.where(isnan(x.phase_data), 0, x.phase_data) for x in ifgs])
    span = array([[x.time_span for x in ifgs]])

    # Update MST in case additional NaNs generated by APS filtering
    if mst is None:  # dummy mst if none is passed in
        mst = ~isnan(obs)
    else:
        mst[isnan(obs)] = 0

    # preallocate empty arrays. No need to preallocation NaNs with new code
    error = np.empty([rows, cols], dtype=float32)
    rate = np.empty([rows, cols], dtype=float32)
    samples = np.empty([rows, cols], dtype=np.int16)

    # pixel-by-pixel calculation.
    # nested loops to loop over the 2 image dimensions
    if parallel:
        res = parmap.map(linear_rate_by_rows, range(rows), cols, mst, nsig, obs,
                     pthr, span, vcm)
        res = np.array(res)
        rate = res[:, :, 0]
        error = res[:, :, 1]
        samples = res[:, :, 2]
    else:
        for i in xrange(rows):
            for j in xrange(cols):
                rate[i, j], error[i, j], samples[i, j] = \
                    linear_rate_by_pixel(j, i, mst, nsig, obs, pthr, span, vcm)

    # overwrite the data whose error is larger than the maximum sigma user threshold
    rate[error > maxsig] = nan
    error[error > maxsig] = nan
    samples[error > maxsig] = nan  # TODO: This step is missing in matlab?

    return rate, error, samples


def linear_rate_by_rows(row, cols, mst, nsig, obs, pthr, span, vcm):
    """
    helper function for parallel 'row' runs
    :param row:
    :param cols:
    :param mst:
    :param nsig:
    :param obs:
    :param pthr:
    :param span: span calculated in linarate function
    :param vcm: temporal vcm matrix
    :return:
    """
    res = np.empty(shape=(cols, 3), dtype=np.float32)
    for col in xrange(cols):
        res[col, :] = linear_rate_by_pixel(
            col, row, mst, nsig, obs, pthr, span, vcm)

    # alternate implementation, check performance for larger images
    # res = map(lambda col:
    #           linear_rate_by_pixel(col, row, mst, nsig, obs, pthr, span, vcm),
    #           range(cols)
    #           )

    return res


def linear_rate_by_pixel(col, row, mst, nsig, obs, pthr, span, vcm):
    # find the indices of independent ifgs for given pixel from MST
    ind = np.nonzero(mst[:, row, col])[0]  # only True's in mst are chosen
    # iterative loop to calculate 'robust' velocity for pixel

    while len(ind) >= pthr:
        # make vector of selected ifg observations
        ifgv = obs[ind, row, col]

        # form design matrix from appropriate ifg time spans
        B = span[:, ind]

        # Subset of full VCM matrix for selected observations
        vcm_temp = vcm[ind, np.vstack(ind)]

        """ start matlab lscov routine """

        # Get the lower triangle cholesky decomposition.
        # V must be positive definite (symmetrical and square)
        T = cholesky(vcm_temp, 1)

        # Incorporate inverse of VCM into the design matrix and observations vector
        A = solve(T, B.transpose())
        b = solve(T, ifgv.transpose())

        # Factor the design matrix, incorporate covariances or weights into the
        # system of equations, and transform the response vector.
        Q, R, _ = qr(A, mode='economic', pivoting=True)
        z = Q.conj().transpose().dot(b)

        # Compute the Lstsq coefficient for the velocity
        v = solve(R, z)

        """end matlab lscov routine"""

        # Compute the model errors; added by Hua Wang, 12/12/2011
        err1 = inv(vcm_temp).dot(B.conj().transpose())
        err2 = B.dot(err1)
        err = sqrt(diag(inv(err2)))

        # Compute the residuals (model minus observations)
        r = (B * v) - ifgv

        # determine the ratio of residuals and apriori variances (Danish method)
        w = cholesky(inv(vcm_temp))
        wr = abs(np.dot(w, r.transpose()))

        # test if maximum ratio is greater than user threshold.
        max_val = wr.max()
        if max_val > nsig:
            # if yes, discard and re-do the calculation.
            ind = delete(ind, wr.argmax())
        else:
            # if no, save estimate, exit the while loop and go to next pixel
            return v[0], err[0], ifgv.shape[0]
    # dummy return for no change
    return np.nan, np.nan, np.nan


if __name__ == "__main__":
    import os
    import shutil
    from subprocess import call

    from pyrate.scripts import run_pyrate
    from pyrate import matlab_mst_kruskal as matlab_mst
    from pyrate.tests.common import SYD_TEST_MATLAB_ORBITAL_DIR, SYD_TEST_OUT
    from pyrate.tests.common import SYD_TEST_DIR
    from pyrate import config as cf
    from pyrate import reference_phase_estimation as rpe
    from pyrate import vcm


    # start each full test run cleanly
    shutil.rmtree(SYD_TEST_OUT, ignore_errors=True)

    os.makedirs(SYD_TEST_OUT)

    params = cf.get_config_params(
            os.path.join(SYD_TEST_MATLAB_ORBITAL_DIR, 'orbital_error.conf'))
    params[cf.REF_EST_METHOD] = 2
    call(["python", "pyrate/scripts/run_prepifg.py",
          os.path.join(SYD_TEST_MATLAB_ORBITAL_DIR, 'orbital_error.conf')])

    xlks, ylks, crop = run_pyrate.transform_params(params)

    base_ifg_paths = run_pyrate.original_ifg_paths(params[cf.IFG_FILE_LIST])

    dest_paths = run_pyrate.get_dest_paths(base_ifg_paths, crop, params, xlks)

    ifg_instance = matlab_mst.IfgListPyRate(datafiles=dest_paths)

    assert isinstance(ifg_instance, matlab_mst.IfgListPyRate)
    ifgs = ifg_instance.ifgs
    for i in ifgs:
        if not i.mm_converted:
            i.convert_to_mm()
            i.write_modified_phase()
    ifg_instance_updated, epoch_list = \
        matlab_mst.get_nml(ifg_instance, nan_conversion=True)
    mst_grid = matlab_mst.matlab_mst_boolean_array(ifg_instance_updated)

    for i in ifgs:
        if not i.is_open:
            i.open()
        if not i.nan_converted:
            i.convert_to_nans()

        if not i.mm_converted:
            i.convert_to_mm()
            i.write_modified_phase()

    if params[cf.ORBITAL_FIT] != 0:
        run_pyrate.remove_orbital_error(ifgs, params)

    refx, refy = run_pyrate.find_reference_pixel(ifgs, params)

    if params[cf.ORBITAL_FIT] != 0:
        run_pyrate.remove_orbital_error(ifgs, params)

    _, ifgs = rpe.estimate_ref_phase(ifgs, params, refx, refy)

    # Calculate interferogram noise
    # TODO: assign maxvar to ifg metadata (and geotiff)?
    maxvar = [vcm.cvd(i)[0] for i in ifgs]

    # Calculate temporal variance-covariance matrix
    vcmt = vcm.get_vcmt(ifgs, maxvar)

    # Calculate linear rate map
    rate, error, samples = run_pyrate.calculate_linear_rate(ifgs, params, vcmt,
                                                 mst=mst_grid)

    MATLAB_LINRATE_DIR = os.path.join(SYD_TEST_DIR, 'matlab_linrate')

    rate_matlab = np.genfromtxt(
        os.path.join(MATLAB_LINRATE_DIR, 'stackmap.csv'), delimiter=',')

    np.testing.assert_array_almost_equal(
        rate[:11, :45], rate_matlab[:11, :45], decimal=4)

    error_matlab = np.genfromtxt(
        os.path.join(MATLAB_LINRATE_DIR, 'errormap.csv'), delimiter=',')
    np.testing.assert_array_almost_equal(
        error[:11, :45], error_matlab[:11, :45], decimal=4)

    samples_matlab = np.genfromtxt(
        os.path.join(MATLAB_LINRATE_DIR, 'coh_sta.csv'), delimiter=',')
    np.testing.assert_array_almost_equal(
        samples[:11, :45], samples_matlab[:11, :45], decimal=4)
