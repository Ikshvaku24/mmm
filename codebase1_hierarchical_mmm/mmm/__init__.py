"""Hierarchical MMM on pre-transformed features.

Entry points live at the codebase root (`run_real_data.py`,
`synthetic_example.py`); everything importable lives here:

    core       config objects, the config.yaml front end, version shims
    data       panel preparation and the pre-model prior builder
    modelling  the PyMC model and the sampler wrapper
    reporting  coefficients, fit, contributions, the benchmark sheet
    checks     convergence, collinearity, assumptions, cross-validation
    pipeline   run_pipeline.run() ties the stages together
"""
