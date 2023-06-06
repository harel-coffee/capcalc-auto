#!/usr/bin/env python
# -*- coding: latin-1 -*-
#
#   Copyright 2016-2021 Blaise Frederick
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#
#
import argparse
import sys

import joblib
import numpy as np
from sklearn.decomposition import PCA, FastICA, SparsePCA
from statsmodels.robust import mad

import capcalc.filter as tide_filt
import capcalc.io as tide_io


def niftidecomp_workflow(
    datafilelist,
    outputroot,
    datamaskname=None,
    decomptype="pca",
    pcacomponents=0.5,
    icacomponents=None,
    trainedmodelroot=None,
    normmethod="None",
    demean=True,
    theprefilter=None,
    sigma=0.0,
    maskthresh=0.25,
):
    print(f"Will perform {decomptype} analysis along the spatial dimension")

    decompaxisnum = 0

    # read in data mask (it must exist)
    print("reading in mask array")
    (
        datamask_img,
        datamask_data,
        datamask_hdr,
        datamaskdims,
        datamasksizes,
    ) = tide_io.readfromnifti(datamaskname)

    xsize, ysize, numslices, mask_timepoints = tide_io.parseniftidims(datamaskdims)
    numspatiallocs = int(xsize) * int(ysize) * int(numslices)

    if mask_timepoints == 1:
        themask = datamask_data.reshape((numspatiallocs))
        proclocs = np.where(themask > maskthresh)
    else:
        print("mask must have only 3 dimensions")
        sys.exit()

    # now read in data
    print("reading in data files")
    numfiles = len(datafilelist)
    for idx, datafile in enumerate(datafilelist):
        print(f"reading {datafile}...")
        (
            datafile_img,
            datafile_data,
            datafile_hdr,
            datafiledims,
            datafilesizes,
        ) = tide_io.readfromnifti(datafile)

        if idx == 0:
            xsize, ysize, numslices, timepoints = tide_io.parseniftidims(datafiledims)
            xdim, ydim, slicethickness, tr = tide_io.parseniftisizes(datafilesizes)
            totaltimepoints = timepoints * numfiles
            originaldatafiledims = datafiledims.copy()
            rs_datafile = np.zeros((numspatiallocs, totaltimepoints), dtype=float)
        else:
            if (not tide_io.checkspacedimmatch(datafiledims, originaldatafiledims)) or (
                not tide_io.checktimematch(datafiledims, originaldatafiledims)
            ):
                print("all input data files must have the same dimensions")
                exit()

        # smooth the data
        if sigma > 0.0:
            print("\tsmoothing data")
            for i in range(timepoints):
                datafile_data[:, :, :, i] = tide_filt.ssmooth(
                    xdim, ydim, slicethickness, sigma, datafile_data[:, :, :, i]
                )

        # prefilter the data
        if theprefilter is not None:
            print("\ttemporally filtering data")
            rs_singlefile = datafile_data.reshape((numspatiallocs, timepoints))
            for i in range(numspatiallocs):
                rs_singlefile[i, :] = theprefilter.apply(1.0 / tr, rs_singlefile[i, :])

        rs_datafile[:, idx * timepoints : (idx + 1) * timepoints] = datafile_data[
            :, :, :, :
        ].reshape((numspatiallocs, timepoints))

    # check dimensions
    if datamaskname is not None:
        print("checking mask dimensions")
        if not tide_io.checkspacedimmatch(datafiledims, datamaskdims):
            print("input mask spatial dimensions do not match image")
            exit()

    print("masking arrays")
    if datamaskname is None:
        datamaskdims = [1, xsize, ysize, numslices, 1]
        themaxes = np.max(rs_datafile, axis=1)
        themins = np.min(rs_datafile, axis=1)
        thediffs = (themaxes - themins).reshape(numspatiallocs)
        proclocs = np.where(thediffs > 0.0)
    procdata = rs_datafile[proclocs, :][0]
    print("data shapes:")
    print(f"\t{rs_datafile.shape[0]} total voxels, {rs_datafile.shape[1]} time points")
    print(f"\t{procdata.shape[0]} valid voxels, {procdata.shape[1]} time points")

    # normalize the individual images
    themean = np.mean(procdata, axis=0)
    if demean:
        print("demeaning array")
        for i in range(procdata.shape[1]):
            procdata[:, i] -= themean[i]

    if normmethod == "None":
        print("will not normalize timecourses")
        thenormfac = themean * 0.0 + 1.0
    elif normmethod == "percent":
        print("will normalize timecourses to percentage of mean")
        thenormfac = themean
    elif normmethod == "stddev":
        print("will normalize timecourses to standard deviation of 1.0")
        thenormfac = np.std(procdata, axis=0)
    elif normmethod == "z":
        print("will normalize timecourses to variance of 1.0")
        thenormfac = np.var(procdata, axis=0)
    elif normmethod == "p2p":
        print("will normalize timecourses to p-p deviation of 1.0")
        thenormfac = np.max(procdata, axis=0) - np.min(procdata, axis=0)
    elif normmethod == "mad":
        print("will normalize timecourses to median average deviate of 1.0")
        thenormfac = mad(procdata, axis=0)
    else:
        print("illegal normalization type")
        sys.exit()
    for i in range(procdata.shape[1]):
        procdata[:, i] /= thenormfac[i]
    procdata = np.nan_to_num(procdata)

    # now perform the decomposition
    if decomptype == "ica":
        print("performing ica decomposition")
        if icacomponents is None:
            print("will return all significant components")
        else:
            print("will return", icacomponents, "components")
        thefit = FastICA(n_components=icacomponents).fit(
            np.transpose(procdata)
        )  # Reconstruct signals
        if icacomponents is None:
            thecomponents = np.transpose(thefit.components_[:])
            print(thecomponents.shape[1], "components found")
        else:
            thecomponents = np.transpose(thefit.components_[0:icacomponents])
            print("returning first", thecomponents.shape[1], "components found")
    else:
        if trainedmodelroot is not None:
            modelfilename = trainedmodelroot + "_pca.joblib"
            print("reading PCA from", modelfilename)
            try:
                thepca = joblib.load(modelfilename)
            except Exception as ex:
                template = (
                    "An exception of type {0} occurred when trying to open {1}. Arguments:\n{2!r}"
                )
                message = template.format(type(ex).__name__, modelfilename, ex.args)
                print(message)
                sys.exit()
        else:
            print("performing pca decomposition")
            if 0.0 < pcacomponents < 1.0:
                print(
                    "will return the components accounting for",
                    pcacomponents * 100.0,
                    "% of the variance",
                )
            elif pcacomponents < 0.0:
                pcacomponents = "mle"
                print("will return", pcacomponents, "components")
            if decomptype == "pca":
                thepca = PCA(n_components=pcacomponents)
            else:
                thepca = SparsePCA(n_components=pcacomponents)

            # save the model
            joblib.dump(thepca, outputroot + "_pca.joblib")

        thefit = thepca.fit(np.transpose(procdata))
        thetransform = thepca.transform(np.transpose(procdata))
        theinvtrans = np.transpose(thepca.inverse_transform(thetransform))

        if pcacomponents < 1.0:
            thecomponents = np.transpose(thefit.components_[:])
            print("returning", thecomponents.shape[1], "components")
        else:
            thecomponents = np.transpose(thefit.components_[0:pcacomponents])

        # stash the eigenvalues
        exp_var_pct = 100.0 * thefit.explained_variance_ratio_

        # save the component images
        outputcomponents = np.zeros((numspatiallocs, thecomponents.shape[1]), dtype="float")
        outputcomponents[proclocs, :] = thecomponents[:, :]
        outputcomponents = outputcomponents.reshape(
            (xsize, ysize, numslices, thecomponents.shape[1])
        )

        # save the coefficients
        outputcoefficients = np.transpose(thetransform)
        # tide_io.writenpvecs(
        #    outputcoefficients * thevar[i], outputroot + "_denormcoefficients.txt"
        # )

        # unnormalize the dimensionality reduced data
        for i in range(totaltimepoints):
            theinvtrans[:, i] = thevar[i] * theinvtrans[:, i] + themean[i]

        print("writing fit data")
        theheader = datafile_hdr
        theheader["dim"][4] = theinvtrans.shape[1]
        outinvtrans = np.zeros((numspatiallocs, theinvtrans.shape[1]), dtype="float")
        outinvtrans[proclocs, :] = theinvtrans[:, :]
        outinvtrans = outinvtrans.reshape((xsize, ysize, numslices, theinvtrans.shape[1]))
    return (
        outputcomponents,
        outputcoefficients,
        outinvtrans,
        exp_var_pct,
        datafile_hdr,
        datafiledims,
        datafilesizes,
    )
