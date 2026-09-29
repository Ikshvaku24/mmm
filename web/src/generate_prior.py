"""Generate the prior file - with codebase 1's own pre-model step.

The app has no prior logic of its own. The old version of this module wrote a
region x variable table of b0/B0 (the old in-house model's format); codebase 1
generates its feature-prior files itself (`mmm.data.prior_builder.build_priors`),
so this only hands it the uploaded files through `src.codebase`.
"""
from src import codebase


def generate_prior(datacube, cfg, mapping=None, share=None, restrict_to=None):
    """Run codebase 1's pre-model step on the uploaded files.

    datacube / mapping / share: (file_bytes, file_name) tuples; mapping and
    share are optional. cfg: the current config.yaml values. restrict_to: a
    prior table whose variables become the list (else: every datacube column).

    Returns a codebase.Outcome; its value holds the case (a-d), every file the
    builder wrote (feature_priors_national.csv, feature_priors_regional.csv,
    prior_calculation.xlsx, 00_warnings.zip) as bytes, and the warnings index.
    """
    return codebase.generate_priors(datacube, cfg, mapping=mapping, share=share,
                                    restrict_to=restrict_to)
