

# Technical Review of the JAX-Enabled Hierarchical MMM

## Comparison with the Proposed Regional Partial-Pooling Design

**Reviewed artifact:** README with codebase.md, including the documented implementations of config.py, data\_prep.py, diagnostics.py, fit.py, model.py, outputs.py, run\_pipeline.py, and synthetic\_example.py.

**Overall conclusion:** The central modelling approach is **not fundamentally different** from the proposed hierarchical MMM. The code implements the same core design: one joint model across all regions, non-centred regional partial pooling, feature-selective hierarchies, structural sign constraints, regional intercepts, Bayesian diagnostics, and posterior contribution reporting. The use of JAX/NumPyro changes the **sampling and execution backend**, not the statistical hierarchy.

There are, however, several important implementation differences and a few corrections recommended before treating holdout coverage, regional comparability, and model uncertainty as production-ready.

## 1. Decision Summary

| Area                                   | Assessment                                                       | Material difference?                                   |
|----------------------------------------|------------------------------------------------------------------|--------------------------------------------------------|
| One joint model across regions         | Matches proposed design                                          | No                                                     |
| Non-centred partial pooling            | Matches proposed design                                          | No                                                     |
| Feature-selective hierarchy            | Matches proposed design                                          | No                                                     |
| Positive and negative sign constraints | Matches, with a strong implementation                            | No                                                     |
| JAX/NumPyro GPU sampling               | Execution enhancement                                            | No statistical difference                              |
| Pre-transformed media                  | Matches the recommended first-stage option                       | No, but carries a known limitation                     |
| KPI scaling                            | Per-region rather than common/global scaling                     | Yes                                                    |
| Feature scaling                        | Per-region rather than common/global scaling                     | Yes                                                    |
| Regional noise                         | Independent regional sigmas, not partially pooled                | Yes, minor to moderate                                 |
| Holdout uncertainty                    | Parameter uncertainty only, not posterior predictive uncertainty | Yes, important                                         |
| HDI implementation                     | Equal-tail percentile interval labelled as HDI                   | Yes, reporting issue                                   |
| Seasonality                            | Shared global Fourier effect                                     | Aligned with recommended first version                 |
| Trend                                  | Hierarchical by region                                           | Slightly more flexible than the initial recommendation |
| Transformation learning                | Outside this codebase                                            | Expected limitation                                    |

## 2. High-Level Architecture

![](768b8ab0d55761b205087c079df1e6e6_img.jpg)

```
graph TD
    A[Stacked panel data: date, region, dv, features] --> B[Validation and time split]
    B --> C[Per-region KPI and feature scaling]
    C --> D[Feature buckets by hierarchy and sign]
    D --> E[One joint PyMC model]
    E --> F[Population parameters]
    E --> G[Regional deviations]
    F --> H[Regional coefficients]
    G --> H
    H --> I[Normal or Student-t likelihood]
    I --> J[PyMC to JAX/NumPyro NUTS]
    J --> K[Posterior diagnostics]
    J --> L[Regional coefficient reports]
    J --> M[Fit and contribution decomposition]
```

The architecture correctly removes the old outer loop that fitted an independent model for each region. All training observations now enter one likelihood, while regional parameters are linked through population distributions.

## 3. Statistical Model Implemented

For observation  $i$ , belonging to region  $g(i)$ , the implemented model is approximately:

- $\varepsilon_i$  follows a Normal or Student-t likelihood.

### Regional intercept

$$\begin{aligned}\mu_\alpha &\sim \mathcal{N}(0, s_\alpha) \\ \tau_\alpha &\sim \text{HalfNormal}(\tau_\alpha) \\ z_{\alpha,g} &\sim \mathcal{N}(0, 1) \\ \alpha_g &\equiv \mu_\alpha + \tau_\alpha z_{\alpha,g}\end{aligned}$$

### Free-sign hierarchical coefficient

$$\begin{aligned}\mu_j &\sim \mathcal{N}(m_j, s_j) \\ \tau_j &\sim \text{HalfNormal}(\tau_j) \\ z_{g,j} &\sim \mathcal{N}(0, 1) \\ \beta_{g,j} &= \mu_j + \tau_j z_{g,j}\end{aligned}$$

#### Positive hierarchical coefficient

$$\begin{aligned}\ell_{g,j} &= \mu_{\ell,j} + \tau_{\ell,j} z_{g,j} \\ \beta_{g,j} &= \exp(\ell_{g,j})\end{aligned}$$

#### Negative hierarchical coefficient

$$\beta_{g,j} = -\exp(\ell_{g,j})$$

This is a strong implementation because structural sign constraints cannot be crossed by posterior draws.

## 4. Is the Approach Different from the Proposed Method?

### 4.1 Core hierarchy: no meaningful difference

Both designs use the same central mechanism:

![](909b8c4010bea29a762d0424d7a8fa81_img.jpg)

```
graph TD
    A[Population effect for feature j] --> B[Regional heterogeneity scale]
    B --> C[Standard Normal regional offsets]
    C --> D[Regional coefficient matrix]
    A --> D
    D --> E[Region-specific contributions]
```

Both implement:

$$\beta_{g,j} = \mu_j + \tau_j z_{g,j}$$

or its log-scale signed equivalent. Consequently:

- weak regions shrink toward the population effect;
- data-rich regions can move farther from the population effect;
- all regions are estimated jointly;
- selected variables can remain globally pooled;
- NUTS propagates uncertainty through population and regional levels.

### 4.2 JAX/GPU: computational difference only

The JAX/NumPyro path changes how NUTS is executed:

![](ea15154205e9b7f2206bad1e33937087_img.jpg)

```
graph LR
    A[PyMC model graph] --> B[JAX translation and JIT compilation]
    B --> C[NumPyro NUTS]
    C --> D{Available device}
    D -- GPU --> E[GPU execution]
    D -- CPU --> F[JIT-compiled CPU execution]
    E --> G[Arviz InferenceData]
    F --> G
```

It does **not** change the hierarchical probability model. Posterior results should target the same distribution, subject to normal numerical and sampler differences.

The fallback to standard PyMC is good operational engineering. The sampling log should always be checked because a successful run does not necessarily mean the GPU path was used.

## 5. Important Difference 1: Per-Region Scaling

The reviewed code standardizes the KPI and features separately within every region.

### KPI scaling

$$y_{g,t}^{(s)} = \frac{y_{g,t} - \bar{y}_g}{s_{y,g}}$$

#### Free feature scaling

$$x_{g,t,j}^{(s)} = \frac{x_{g,t,j} - \bar{x}_{g,j}}{s_{x,g,j}}$$

#### Sign-constrained feature scaling

$$x_{g,t,j}^{(s)} = \frac{x_{g,t,j}}{x_{g,j}^+}$$

The earlier recommendation used common training scaling across the stacked dataset. That would produce:

$$x_{g,t,j}^{(s)} = \frac{x_{g,t,j} - \bar{x}_j}{s_{x,j}}$$

### Consequence

With per-region scaling, partial pooling occurs over **standardized regional effects**, not directly over coefficients expressed in common original units.

For a free variable, the original-unit slope is:

$$\beta_{g,j}^{(original)} = \beta_{g,j}^{(scaled)} \frac{s_{y,g}}{s_{x,g,j}}$$

For a scale-only signed variable:

$$\beta_{g,j}^{(original)} = \beta_{g,j}^{(scaled)} \frac{s_{y,g}}{x_{g,j}^+}$$

Therefore, equal scaled coefficients in two regions do not imply equal original-unit response.

### Is this wrong?

No. It represents a different estimand.

- Per-region scaling** pools relative effects measured in regional KPI-standard units.
- Global scaling** pools coefficients over a common transformed scale across regions.

Per-region scaling can improve numerical geometry and prevent large regions from dominating purely because of scale. It is suitable when the business interpretation is “response relative to normal activity and KPI variability within each region.”

Global scaling is preferable when regional coefficients must be directly comparable in the same units before post-processing.

### Recommendation

Keep per-region scaling if it is intentional, but report both:

- scaled coefficients used by the hierarchy;
- coefficients converted to original-unit response.

The coefficient report should explicitly label the units. At present, readers may mistakenly interpret regional coefficient differences as direct original-unit differences.

## 6. Important Difference 2: Regional Noise Is Not Partially Pooled

The model defines:

$$\sigma_g \sim \text{HalfNormal}(1)$$

independently for each region.

The proposed design used partial pooling on log noise:

$$\log \sigma_g = \mu_\sigma + \tau_\sigma z_{\sigma,g}$$

This difference does not invalidate the model. However, a region with relatively few training observations receives no direct borrowing of strength for residual noise.

### Recommended alternative

```
mu_log_sigma = pm.Normal("mu_log_sigma", 0.0, 1.0)
tau_log_sigma = pm.HalfNormal("tau_log_sigma", 0.5)
z_log_sigma = pm.Normal("z_log_sigma", 0.0, 1.0, dims="region")

sigma_region = pm.Deterministic(
    "sigma_region",
    pm.math.exp(mu_log_sigma + tau_log_sigma * z_log_sigma),
    dims="region",
)
```

This is most useful when observation counts vary materially across regions. If every region has a long, balanced time series, independent regional sigmas may be adequate.

## 7. Important Difference 3: Holdout Bands Are Not Posterior Predictive Bands

This is the most important correction.

compute\_decomposition constructs draws of the conditional mean:

$$\mu_t^{(s)} = \alpha_{g(t)}^{(s)} + \sum_j x_{t,j} \beta_{g(t),j}^{(s)} + \text{seasonality}^{(s)} + \text{trend}^{(s)}$$

The reported interval is then calculated from draws of  $\mu_t$ . It captures parameter uncertainty, but it does not add residual variation from the likelihood.

### Current versus required uncertainty

![](8ac55557020b7bf5ac750c9d25ecbec4_img.jpg)

```
graph TD
    A[Posterior parameter draws] --> B[Conditional mean draws mu]
    B --> C[Current fitted bands]
    B --> D[Add likelihood noise using sigma and nu]
    D --> E[Posterior predictive draws y-tilde]
    E --> F[Predictive coverage bands]
```

### Why this matters

The existing `coverage_90_pct` measures how often actual observations lie inside an interval for the latent conditional mean. That is not standard 90% predictive coverage and will usually be too narrow.

### Recommendation

- Maintain two separate outputs:
- `mean_response_interval_90`: uncertainty around the expected response;
  - `posterior_predictive_interval_90`: uncertainty for future observations.

Use the second interval for holdout coverage.

A direct draw-based implementation can use posterior draws of `sigma_region` and `nu`, or the model can be rebuilt with mutable holdout inputs and passed to `pm.sample_posterior_predictive`.

## 8. Important Difference 4: Equal-Tail Intervals Are Labelled as HDIs

The `_stats` function uses percentiles:

```
lo, hi = np.percentile(x, [5, 95])
```

This is a 90% **equal-tail interval**, not necessarily a 90% highest-density interval.

For symmetric unimodal posteriors, the results may be similar. For skewed log-normal signed coefficients, the distinction can be meaningful.

### Correct options

Use accurate naming:

```
eti_5
eti_95
```

or compute an actual HDI:

```
import arviz as az
lo, hi = az.hdi(x, hdi_prob=0.90)
```

This is a reporting correction, not a change to the model.

## 9. Pre-Transformed Media: Aligned but Limited

The code accepts adstocked, saturated, and lagged features from preprocessing. This matches the recommended first implementation:

![](fac96b142bfda505ff16275c222f3af2_img.jpg)

```
graph LR
    A[Raw media] --> B[External adstock]
    B --> C[External saturation or Hill transform]
    C --> D[Scaled transformed feature]
    D --> E[Hierarchical regional coefficient]
```

The resulting model estimates conditional response coefficients for fixed transformations.

### Limitation

If the externally selected transformation is wrong, the Bayesian regression cannot detect the missing transformation uncertainty. A well-fitting coefficient can compensate for a poor adstock or saturation choice.

The main next-stage alternative is:

![](739503032afbc07b39a439a24c98560f_img.jpg)

```
graph LR
    A[Raw media] --> B[Learned adstock parameter]
    B --> C[Learned saturation parameter]
    C --> D[Transformed media inside model]
    D --> E[Regional partial-pooling coefficient]
```

The current split into “Codebase 1” and a later full MMM is therefore sensible.

## 10. Fourier Seasonality and Trend

### Fourier seasonality

The same Fourier coefficient is shared across regions:

$$S_t = \sum_{k=1}^K [\gamma_{k,s} \sin(2\pi kt/P) + \gamma_{k,c} \cos(2\pi kt/P)]$$

This is aligned with a conservative first implementation. It avoids adding many regional seasonal parameters.

### Regional trend

The trend is partially pooled:

$$\begin{aligned}b_g &= \mu_b + \tau_b z_{b,g} \\ Trend_{g,t} &= b_g t\end{aligned}$$

This is slightly more flexible than a shared initial trend and is reasonable when regions genuinely have different long-term trajectories.

### Recommended future check

A single annual period of 365.25 days is appropriate only when the date field and observation cadence produce the intended calendar cycle. For weekly data, the day-based construction is generally coherent. For monthly data with irregular month lengths it remains workable, but a month-index formulation may be easier to interpret.



## 11. Feature Bucketing Is a Strong Improvement

The six buckets are:

hierarchical positive  
hierarchical negative  
hierarchical free  
global positive  
global negative  
global free

![Flowchart for Feature Bucketing](68d50e85fb8de4fae0e0eafaf20e63c0_img.jpg)

```
graph TD
    A[Feature specifications] --> B{Hierarchical?}
    B -- Yes --> C{Sign}
    B -- No --> D{Sign}
    C --> E[hierarchical positive]
    C --> F[hierarchical negative]
    C --> G[hierarchical free]
    D --> H[global positive]
    D --> I[global negative]
    D --> J[global free]
```

This design is preferable to constructing one scalar random variable per feature and region because it:

- produces fewer graph-building operations;
- gives JAX larger vectorized blocks;
- keeps coordinates and posterior extraction systematic;
- allows feature-selective pooling and sign constraints;
- improves maintainability.

Flowchart for Feature Bucketing

## 12. All-Zero Regional Features

The README states that an all-zero regional feature receives zero contribution and its coefficient shrinks toward the population mean. That is conceptually correct.

For a region where  $x_{g,t,j} = 0$  for every training period:

$$Contribution_{g,t,j} = x_{g,t,j} \beta_{g,j} = 0$$

The regional coefficient has no region-specific likelihood information, so its posterior is determined by the population hierarchy and the heterogeneity distribution.

### Reporting caution

The coefficient for that region should not be presented as regionally learned. Add support columns to the coefficient report:

- number of non-zero observations;
- feature mean and standard deviation;
- first and last active date;
- posterior-prior contraction;
- a flag such as `region_feature_data_support = none/weak/adequate`.

## 13. Contribution Decomposition

The decomposition is internally coherent with regional KPI scaling.

On the scaled KPI axis:

$$c_{i,j}^{(s)} = x_{i,j}^{(s)} \beta_{g(i),j}^{(s)}$$

Back on the original KPI axis:

$$c_{i,j}^{(original)} = s_{g(i)} c_{i,j}^{(s)}$$

The baseline is restored as:

$$Baseline_i^{(original)} = \mu_{g(i)} + s_{g(i)} Baseline_i^{(s)}$$

Thus:

$$\hat{y}_i^{(original)} = Baseline_i^{(original)} + \sum_j c_{i,j}^{(original)}$$

### Recommended additions

1. Separate train and holdout contribution totals.
2. Report posterior intervals for time-level contributions where feasible.
3. Add spend before calculating ROI or ROAS.
4. Distinguish contribution from causal incrementality unless identification assumptions are defended.
5. Export transformed-feature values and scale factors with reports for auditability.

## 14. Diagnostics Assessment

The diagnostic design is substantially stronger than the old model engine.

### Correctly included

- R-hat;
- bulk effective sample size;
- divergences;
- BFMI;
- trace plots for problematic parameters;
- prior-posterior contraction;
- prior predictive checks;
- posterior direction probability;
- interval exclusion of zero.

### Recommended additions

- tail ESS in the convergence report;
- maximum tree depth or tree-depth saturation;
- E-BFMI by chain rather than only the minimum;
- Pareto-smoothed LOO when log likelihood is retained;
- posterior predictive residual checks by region and time;
- sensitivity runs for the regional heterogeneity priors;
- calibration of predictive intervals on holdout data.

The current sampler call sets:

```
idata_kwargs={"log_likelihood": False}
```

If LOO or WAIC comparison is required, retain pointwise log likelihood or compute it in a separate evaluation run.

## 15. GPU and Sampling Considerations

A GPU does not automatically guarantee a faster run. Performance depends on:

- number of observations;
- number of regions and features;
- number of chains;
- vectorized versus parallel chain strategy;
- JIT compilation overhead;
- GPU memory;
- model geometry and divergence rate.

### Operational flow

![Operational flowchart for GPU and Sampling Considerations](ea15154205e9b7f2206bad1e33937087_img.jpg)

```
graph TD
    A[Start sampling request] --> B[Inspect JAX devices]
    B --> C{NumPyro available?}
    C -- Yes --> D[Compile model with JAX]
    C -- No --> F[Fallback to PyMC]
    D --> E{GPU device visible?}
    E -- Yes --> G[Run on GPU]
    E -- No --> H[Run JIT CPU]
    G --> I[Write backend and timing log]
    H --> I
    F --> I
```

Operational flowchart for GPU and Sampling Considerations

### Recommendation

Record the following in every run manifest:

- PyMC, PyTENSOR, JAX, NumPyro, ArviZ, NumPy, and pandas versions;
- device list;
- sampler selected and sampler actually used;
- chain method;
- compilation time;
- sampling time;
- seed;
- number of draws, tuning steps, and chains;
- model configuration hash;
- input data hash.

## 16. Recommended Corrections by Priority

### Priority 1: Fix predictive coverage

Generate likelihood-level posterior predictive draws and calculate holdout coverage from those draws.

### Priority 2: Clarify coefficient units

Export scaled and original-unit coefficients, with explicit formulae and labels.

### Priority 3: Correct interval terminology

Rename percentile intervals to equal-tail intervals or calculate true HDIs.

### Priority 4: Decide whether regional noise should be pooled

Use hierarchical log-sigma when regional sample sizes are unequal or sigma estimates are unstable.

### Priority 5: Add regional feature-support diagnostics

Do not imply that a region-specific coefficient was learned where the feature was inactive.

### Priority 6: Separate latent-mean fit from predictive fit

Report:

- conditional mean fit;
- posterior predictive fit;
- process-noise-inclusive holdout intervals.

### Priority 7: Add configuration validation

Recommended checks include:

- no feature appears in multiple buckets;
- negative raw values are rejected or explicitly supported for scale-only signed features;
- each region has enough training observations;
- holdout periods do not consume an entire region;
- Fourier order is sensible relative to training length;
- prior scales are finite and positive;
- regional scale factors are recorded and non-degenerate.

## 17. Improved End-to-End Production Flow

![Improved End-to-End Production Flowchart](c39da67a0e8aff7bc4f1505be807de25_img.jpg)

```
graph TD
    A[Raw stacked panel] --> B[Schema and support validation]
    B --> C[Time-based training and holdout split]
    C --> D[Fit transformations and scaling on training data only]
    D --> E[Create feature buckets]
    E --> F[Prior predictive simulation]
    F --> G{Prior behaviour plausible?}
    G -- No --> H[Revise priors]
    G -- Yes --> I[Build joint hierarchical model]
    H --> F
    I --> J[JAX/NumPyro or PyMC NUTS]
    J --> K{Convergence acceptable?}
    K -- No --> L[Reparameterise or revise model]
    K -- Yes --> M[Conditional mean decomposition]
    L --> F
    L --> I
    M --> N[Regional coefficient and contribution reports]
    M --> O[Posterior predictive simulation]
    N --> P[Support and prior-sensitivity flags]
    O --> Q[Predictive interval coverage]
    O --> R[Train and holdout metrics]
    P --> S[Model approval decision]
    Q --> S
    R --> S
```

Improved End-to-End Production Flowchart

## 18. Suggested Model Evolution

### Version 1: Current code with reporting corrections

- Pre-transformed media.
- Per-region scaling.
- Partially pooled intercept and selected coefficients.
- Global Fourier seasonality.
- Optional regional trend.
- Normal or Student-t likelihood.
- JAX/NumPyro backend.
- Correct posterior predictive intervals.

### Version 1.1: Noise pooling and support controls

- Hierarchical regional sigma.
- Support flags for every region-feature pair.
- Original-unit coefficient export.
- Actual HDIs.
- Tail ESS and tree-depth reporting.

### Version 2: Learned media transformations

$$Media_{g,t,j}^* = Hill(Adstock(Media_{g,t,j}; \theta_j); \kappa_j, \lambda_j)$$

Initially keep  $\theta_j$ ,  $\kappa_j$ , and  $\lambda_j$  common by channel, while retaining regional partial pooling in  $\beta_{g,j}$ .

### Version 3: Selective transformation hierarchy

Only where data support is strong:

$$\text{logit}(\theta_{g,j}) = \mu_{\theta,j} + \tau_{\theta,j} z_{g,j}$$

Avoid making coefficient, adstock, saturation, trend, and seasonality all region-specific simultaneously without strong identification evidence.

## 19. Final Assessment

The implementation is **substantively aligned** with the proposed hierarchical MMM. It is not an alternative modelling philosophy. It is a more engineered and GPU-capable implementation of the same regional partial-pooling concept.

### Strongest parts

1. One joint regional model.
2. Correct non-centred hierarchy.
3. Structural positive and negative coefficient constraints.
4. Vectorized feature buckets.
5. JAX/NumPyro execution with fallback.
6. Training-only scaling statistics.
7. Time-based holdout split.
8. Removal of invalid mean-over-MCSE p-values.
9. Prior predictive and prior-posterior diagnostics.
10. Posterior-draw-based contribution decomposition.

### Material differences to retain intentionally or revise

1. **Per-region scaling**: valid, but changes the estimand and coefficient comparability.
2. **Independent regional sigma**: acceptable but less pooled than the proposed design.
3. **Fixed upstream transformations**: appropriate for Version 1 but excludes transformation uncertainty.
4. **Global seasonality and regional trend**: reasonable first-stage design choices.

### Corrections required before production interpretation

1. Do not call latent-mean intervals posterior predictive intervals.
2. Do not use latent-mean intervals for predictive coverage.
3. Do not label percentile intervals as HDIs.
4. Label coefficient units and provide original-unit conversions.
5. Flag coefficients for region-feature combinations with no or weak data support.

**Bottom line:** Keep the architecture. Correct the uncertainty reporting, make the scaling estimand explicit, and consider partial pooling for regional noise. After those changes, the codebase will closely implement the intended hierarchical regional MMM while adding a practical JAX/GPU execution layer.

## 20. Reviewed Code Map


```
graph LR
    A[config.py] --> B[Feature hierarchy, sign, and prior configuration]
    C[data_prep.py] --> D[Validation, holdout split, regional scaling]
    E[model.py] --> F[Joint hierarchical probability model]
    G[fit.py] --> H[JAX/NumPyro or PyMC sampling]
    I[diagnostics.py] --> J[Convergence and prior diagnostics]
    K[outputs.py] --> L[Coefficients, fit, and contributions]
    M[run_pipeline.py] --> N[End-to-end orchestration]
    O[synthetic_example.py] --> P[Smoke and recovery test]
```