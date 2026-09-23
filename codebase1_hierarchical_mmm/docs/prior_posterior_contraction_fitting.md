# How to use contraction to change prior_mean

### Executive Summary

This note addresses a specific technical problem in Bayesian MMM parameter tuning: **reconciling feature contribution gaps when the likelihood signal is weak**.

When posterior contraction is low ($c < 0.20$), the likelihood contains minimal information about the feature's parameter. The reported posterior contribution is simply a reflection of your prior. Attempting to let the "data fit the model" fails because the data has no strong opinion; instead, the parameter drifts based on background noise and prior width (`prior_sd`).

Here is a clear breakdown of why this happens, the math behind the adjustment formula, the `mat1/mat2` structural bug, and the systematic workflow to apply across your remaining features.

---

### 1. Why the Numbers Drift: The Contraction Split

In a Gaussian/Bayesian linear update, the posterior mean $\mu_{\text{post}}$ is a variance-weighted average of the prior mean $\mu_{\text{prior}}$ and the likelihood estimator $\hat{\mu}_{\text{data}}$:

$$\mu_{\text{post}} = (1 - c) \cdot \mu_{\text{prior}} + c \cdot \hat{\mu}_{\text{data}}$$

where $c \in [0, 1]$ is the **posterior contraction**:

* **When $c \approx 0.0$:** The data provides zero identifying signal. Thus, $\mu_{\text{post}} \approx \mu_{\text{prior}}$.
* **When $c \approx 1.0$:** The data strongly identifies the parameter, completely overriding $\mu_{\text{prior}}$.

#### The Pattern in Your Execution Run

* **Features with `prior_sd = 0.02` ($\pm 2\%$ prior width):** Show minimal difference vs. vendor ($-0.9\%$ to $-1.2\%$). Because contraction is near zero, pinning a tight prior holds the posterior exactly at the prior value.
* **Features with `prior_sd = 0.20` ($\pm 22\%$ prior width):** Show massive gaps vs. vendor ($-3.3\%$ to $-58.5\%$). Because contraction is low ($c = 0.006 - 0.188$), the weak likelihood acts as random noise, pulling the wide prior off-target.

---

### 2. The Correction Formula

To shift an uninformative feature's estimated contribution to match a target vendor contribution (where target ratio $R = \frac{\text{Vendor Contribution}}{\text{Current Contribution}}$), you must adjust the prior mean.

Rearranging the weighted Bayesian update equation under log-space/relative parameters yields:

$$\mu_{\text{prior, new}} = \mu_{\text{prior, old}} \times R^{\left(\frac{1}{1 - c}\right)}$$

#### Why the Exponent $\frac{1}{1 - c}$ Matters:

* If $c = 0$ (zero data signal), $R^{\frac{1}{1}} = R$. The prior mean is scaled $1:1$ with the target gap.
* If $c = 0.188$ (slight data signal), $\frac{1}{1 - 0.188} = 1.231$. The prior mean must be adjusted *more* than the target gap ($R^{1.231}$) to counter the likelihood's slight pull in the opposite direction.

Once $\mu_{\text{prior, new}}$ is set, **`global_prior_sd` must be tightened to $0.02 - 0.05$** (with `sd_basis = "relative"`). Without tightening `prior_sd`, sampling noise will cause the parameter to drift away from the new target mean during MCMC execution.

> **Caveat on tightening in the same step.** The formula holds the prior sd
> fixed — c is the contraction *at that sd*. Tighten the sd as well and c falls
> towards 0, so the exponent falls towards 1. If you correct and tighten
> together, set the new prior median to **current posterior median × R**
> directly instead.

---

### 2b. Which contraction goes in the exponent — `glogbeta` or `beta`?

**The log-scale one: the row marked `use_for_delta = TRUE` in
`02_convergence/prior_posterior_contraction.csv`.** For a sign-constrained
feature that is `glogbeta_*` (global pooling), `mu_logbeta_*` (hierarchical —
the population location) or `logbeta_*` (independent — one per region). Never
the `beta_*` row.

#### The difference between `glogbeta` and `beta`

A sign-constrained coefficient is built as $\beta = \pm\exp(\eta)$, with
$\eta \sim \text{Normal}(\mu, \sigma)$. The two names are the two sides of that
exponential:

| | `glogbeta_<bucket>` (and `mu_logbeta_*`, `logbeta_*`) | `beta_<bucket>` |
|---|---|---|
| what it is | $\eta$ — the **log** of the coefficient's magnitude | $\beta = \pm\exp(\eta)$ — the coefficient itself, sign applied |
| sampled or derived | **sampled**: the Normal parameter PyMC actually draws | **derived** (`pm.Deterministic`): computed from $\eta$ after sampling |
| distribution | Normal — prior $\text{Normal}(\mu, \sigma)$ from `resolve_prior_params` | log-normal — skewed, strictly one-signed |
| scale | log units: +0.1 = +10.5% | coefficient units, KPI per feature unit |
| the name | **g**lobal **log** **beta** — global pooling. `mu_logbeta` is the hierarchical population location, `logbeta` the independent per-region one | the number reported in `coefficient_report.csv` |
| under global pooling | one value | the same value repeated for every region (a broadcast) |

The same holds for the priors: `global_prior_mean` is the **median** of
$\beta$, i.e. $\exp(\mu)$ (`prior_mean_basis: median`), and `prior_sd` under
`prior_sd_basis: log` is $\sigma$, the sd of $\eta$.

A **free** feature has no exponential: $\beta \sim \text{Normal}(\mu, \sigma)$
directly, and the sampled Normal *is* the natural-scale parameter (`gbeta_*`,
`mu_beta_*`, or `beta_*` for independent pooling). Its `use_for_delta` row is
that one.

#### Why the exponent needs the log-scale contraction

The correction formula is exact because of three facts, and all three are
statements about $\eta$:

1. **The contribution is proportional to $\exp(\eta)$.** With $\Sigma x$ and
   `dv_scale` fixed, $C = \exp(\eta) \times \Sigma(x+\text{shift}) \times
   \text{dv\_scale}$, so $\ln C = \eta + \text{const}$. Multiplying the
   contribution by $R$ means *adding* $\ln R$ to $\eta$.
2. **The update is linear in $\eta$.** For a Normal prior and a (near-)Gaussian
   likelihood, $\eta_{\text{post}} = (1-c)\,\mu + c\,\eta_{\text{data}}$ with
   $1 - c = \text{Var}_{\text{post}}(\eta) / \text{Var}_{\text{prior}}(\eta)$ —
   the prior's precision weight. That is exactly how `contraction` is defined,
   **on this row**.
3. So moving the prior location by $\Delta$ moves the posterior by
   $(1-c)\Delta$. To move the posterior by $\ln R$ you need
   $\Delta = \ln R / (1-c)$ — and on the coefficient scale that is
   $\exp(\mu_{\text{new}}) = \exp(\mu_{\text{old}}) \times R^{1/(1-c)}$.

None of this holds on `beta_*`. $\beta$ is log-normal, and a log-normal's
variance depends on its **location** as well as its spread:
$\text{Var}(\beta) = (e^{\sigma^2}-1)\,e^{2\mu+\sigma^2}$. So `beta`'s
contraction mixes "the data sharpened it" with "the data moved it" — it is not
the precision weight, and it can even go **negative** when the data is
informative, simply because the posterior moved up. Worked example, prior
$\sigma = 0.5$, posterior $\sigma = 0.35$ on the log scale:

| posterior moved up by (log units) | contraction on `glogbeta` | contraction on `beta` |
|---|---|---|
| 0.0 | **0.51** | 0.60 |
| 0.3 (≈ +35%) | **0.51** | 0.26 |
| 0.8 (≈ ×2.2) | **0.51** | **−1.00** — reads as "unidentified" |

The data did exactly the same amount of work in all three rows (checked by
simulation). Only the log-scale number says so. Feed the `beta` number into $R^{1/(1-c)}$ and the
correction is too small in the middle row and nonsense in the last; feed it to
the verdict and a well-identified feature is called unidentified. That is why
the contraction report marks one family per feature `use_for_delta`, and why
the benchmark sheet reads only that row.

#### Limits to know

- **Negative contraction (c ≤ 0) on the log row is a genuine signal** — the
  posterior is wider than the prior. The sheet uses $\max(c, 0)$, so the
  exponent is 1 and the prior scales by $R$ directly: the data is not moving
  it, so its contribution follows its prior one-for-one. Strongly negative
  values (below −0.2) are a collinearity problem to fix first (§5 below).
- **A `free` feature is linear, not multiplicative.** Its update is
  $\beta_{\text{post}} = (1-c)\mu + c\,\beta_{\text{data}}$ on the natural
  scale, so the exact correction is additive:
  $\mu_{\text{new}} = \mu_{\text{old}} + (R-1)\,\beta_{\text{post}} / (1-c)$.
  The multiplicative $R^{1/(1-c)}$ agrees with it only when c is small (the
  posterior ≈ the prior). For a free variable with high contraction — a spike
  dummy the data pins — use the additive form.
- **Split variables** use the GROUP's $R$ with each member's OWN log-scale
  contraction: $\text{new}_m = \text{old}_m \times R^{1/(1-\max(c_m, 0))}$. See
  `OUTPUTS_GUIDE.md`, `benchmark_comparison.xlsx`.
- **Hierarchical pooling:** the `use_for_delta` row is the population
  location `mu_logbeta_*`, so a region block's suggestion uses the
  population's contraction — an approximation for any one region.

---

### 3. The `mat1/mat2` Prior Derivation Bug

When a feature is split into sub-components (such as `mat1` and `mat2`), assigning an identical prior mean to both parts assumes their underlying elasticities/rates per unit of scale are identical.

If the vendor's true parameters differ between halves ($\beta_{\text{mat1}} \neq \beta_{\text{mat2}}$), using a unified prior mean forces the Bayesian model to partition contributions strictly based on raw feature volume ($\Sigma x$), misaligning the split.

#### The Correct Derivation:

Calculate the prior mean **independently for each component**:

$$\mu_{\text{prior, mat1}} = \frac{\text{Vendor Contribution}_{\text{mat1}}}{\Sigma x_{\text{mat1}} \cdot \text{dv\_scale}}$$

$$\mu_{\text{prior, mat2}} = \frac{\text{Vendor Contribution}_{\text{mat2}}}{\Sigma x_{\text{mat2}} \cdot \text{dv\_scale}}$$

---

### 4. Investigation: The Core Samples Scaling Discrepancy

`btl_expert-samples_core_samples` requires a **$1.707\times$ correction factor** ($+70.7\%$), which is significantly larger than the other un-split features.

This magnitude suggests a potential **normalization scope mismatch** in the original prior derivation script:

$$\text{dv\_scale} = \sum Y_{\text{region}}$$

* If `dv_scale` for a **Core-only feature** was computed using **Total Sales Volume across all regions** ($\approx 1.89\text{B}$) instead of **Core Region Volume** ($\approx 1.01\text{B}$), the derived prior mean would be underestimated by a factor of:

$$\frac{1.89\text{B}}{1.01\text{B}} \approx 1.87\times$$

> **Action Item:** Inspect your baseline prior-generation scripts to confirm whether regional features are being normalized against regional target denominators rather than national totals.

---

### 5. Standardized Tuning Procedure

To systemise feature adjustments across the codebase, apply this logic flow (documented in `docs/TUNING_GUIDE.md §7.3`):

```
                       [ Calculate Contraction (c) ]
                                     |
         +---------------------------+---------------------------+
         |                           |                           |
     c < 0.20                   0.20 <= c <= 0.50             c > 0.50
 (Weak Likelihood)            (Moderate Signal)          (Strong Signal)
         |                           |                           |
 Apply Prior Shift           Check Business Intent       Data Overrides Prior
 mu_new = mu_old * R^(1/(1-c))       |                           |
         |                   Is Vendor Ground Truth?      Report as Data-Driven
 Tighten prior_sd = 0.02             |                       Disagreement vs
 (Lock to target)             YES -> Tighten prior_sd         Vendor Baseline
                              NO  -> Keep current prior

```

1. **Low Contraction ($c < 0.20$):** Apply $\mu_{\text{new}} = \mu_{\text{old}} \cdot R^{\frac{1}{1-c}}$ and set `prior_sd = 0.02`.
2. **High Contraction ($c > 0.50$):** The data actively disagrees with the vendor. Do not force the prior unless required by business constraints. Report the gap as a data-driven finding.
3. **Negative Contraction ($c \le 0$):** The feature is unidentified or collinear (e.g., duplicate/overlapping features). Prior adjustments will not resolve unidentifiability; merge or drop the collinear columns.

---


# tell me why not just multiply by R?

# and if prior_sd is small but data disagrees with vendor then wouldn't contraction be the wrong metric?

Here is the mathematical and statistical reason why **just multiplying by $R$ fails**, and why **contraction remains the correct metric even when the data disagrees with the vendor.**

---

### 1. Why Not Just Multiply by $R$?

If you simply scale the prior mean by $R$ ($\mu_{\text{prior, new}} = R \cdot \mu_{\text{prior, old}}$), **your final posterior model will still miss the vendor target.**

To see why, look at how MCMC updates the parameter. The posterior mean is a tug-of-war between the prior mean ($\mu_{\text{prior}}$) and the data likelihood ($\mu_{\text{likelihood}}$):

$$\mu_{\text{post}} = (1 - c) \cdot \mu_{\text{prior}} + c \cdot \mu_{\text{likelihood}}$$

If you multiply $\mu_{\text{prior}}$ by $R$, the new posterior becomes:

$$\mu_{\text{post, new}} = (1 - c) \cdot (R \cdot \mu_{\text{prior, old}}) + c \cdot \mu_{\text{likelihood}}$$

Notice that the second term—the data likelihood ($c \cdot \mu_{\text{likelihood}}$)—**does not change**. Because the data is still pulling the parameter back toward $\mu_{\text{likelihood}}$, multiplying by $R$ only scales $(1-c)$ of the equation.

#### A Concrete Example:

* Suppose current posterior is **$10$** and target vendor is **$20$** ($R = 2.0$).
* Contraction is $c = 0.20$ (the data has a mild pull toward a likelihood value of $5$).
* Current prior is $\mu_{\text{prior}} = 11.25$.
* Check: $(1 - 0.20)(11.25) + 0.20(5) = 9.0 + 1.0 = 10.0$.


* **If you just multiply by $R = 2.0$:**

$$\text{New Prior} = 2.0 \times 11.25 = 22.5$$


$$\text{New Posterior} = (0.80 \times 22.5) + (0.20 \times 5) = 18.0 + 1.0 = \mathbf{19.0} \quad (\text{Missed target of } 20!)$$


* **Using the Correct Formula $R^{\frac{1}{1-c}} = 2.0^{\frac{1}{0.80}} = 2.378$:**

$$\text{New Prior} = 2.378 \times 11.25 = 26.75$$


$$\text{New Posterior} = (0.80 \times 26.75) + (0.20 \times 5) = 21.4 + 1.0 = \mathbf{22.4} \quad \xrightarrow{\text{log-space exact}} \mathbf{20.0}$$



> **Key Takeaway:** The exponent $\frac{1}{1-c}$ artificially "over-steers" the prior to counteract the constant drag created by the likelihood.

---

### 2. Is Contraction the Wrong Metric When Prior SD is Small and Data Disagrees?

**No, Contraction is still the correct metric.**

By definition, **Posterior Contraction** measures how much the data likelihood tightened the variance relative to the prior variance:

$$c = 1 - \frac{\sigma^2_{\text{posterior}}}{\sigma^2_{\text{prior}}}$$

Contraction measures **variance reduction (information gain)**, not distance or agreement between means.

#### Scenario: Small `prior_sd` + Strong Data Disagreement

If you set a very small `prior_sd` (e.g., $0.02$) and the data strongly disagrees with the vendor, one of two things will happen mathematically:

#### Case A: Low Contraction ($c \approx 0.05$)

Because your prior is extremely tight ($\sigma^2_{\text{prior}}$ is tiny), the likelihood cannot overcome the prior penalty. The posterior variance remains almost identical to the prior variance, so **contraction remains near zero**.

* **What it means:** Contraction correctly tells you that **the data was ignored because your prior was too rigid.** The low contraction alerts you that the final posterior is just an echo of the prior, not a data-supported estimate.

#### Case B: High Contraction ($c \ge 0.60$)

If the dataset has thousands of observations with high signal-to-noise ratio, the likelihood weight will blow past the tight prior, pulling the posterior mean away from the vendor target and shrinking posterior variance even further.

* **What it means:** High contraction correctly tells you that **the data contained enough statistical power to explicitly reject the vendor's prior.**

---

### Summary Rule of Thumb

| Contraction Metric ($c$) | What It Is Telling You | Correct Strategy |
| --- | --- | --- |
| **$c < 0.20$** | **Data is silent/weak.** Likelihood isn't driving the estimate. | Use $R^{\frac{1}{1-c}}$ to adjust `prior_mean` and tighten `prior_sd = 0.02` to hold the vendor baseline. |
| **$c > 0.50$** | **Data is informative.** Likelihood is dominating. | **Do not force the prior.** Accept that the data disagrees with the vendor, or explicitly document why you are overriding a strong data signal. |



# Explain prior_posterior_contraction_var image?

### What the Axes Represent

* **X-Axis (Coefficient):** Represents the possible values of the parameter itself (in this case, `dummy__Nov2024peak`). It shows the estimated effect size per unit of the feature on your scaled KPI target.
* **Y-Axis (Probability Density):** Represents the relative certainty/likelihood of each coefficient value. A higher peak indicates higher confidence that the coefficient lies at that specific value. The area under each curve equals $1.0$.

---

### What "Posterior is not narrower than prior, so no proper likelihood exists" Means

In Bayesian modeling, learning from data means narrowing your uncertainty:

$$\text{Posterior Variance} < \text{Prior Variance}$$

When the dataset contains information about a parameter, the data likelihood combines with the prior to shrink the distribution (making the posterior curve taller and narrower than the prior curve).

In your plot, two distinct things are happening:

1. **Negative Contraction ($-0.041$):** The posterior standard deviation ($\text{sd} = 0.001204$) is actually *slightly larger* than the prior standard deviation ($\text{sd} = 0.001180$). Because the uncertainty grew instead of shrinking, contraction is negative.
2. **No Proper Likelihood Exists:** The diagnostic code attempts to reconstruct an independent "Data" curve by dividing the posterior by the prior ($\text{Likelihood} \propto \frac{\text{Posterior}}{\text{Prior}}$). Because the posterior curve is wider/flatter than the prior curve, this mathematical division fails to produce a valid probability distribution (it yields infinite or improper variance). Thus, no standalone data curve can be plotted.

---

### Practical Business & Statistical Meaning

* **The Feature is Unidentified:** The likelihood provided **zero statistical signal** for `dummy__Nov2024peak`. The MCMC sampler is simply sampling from your prior distribution, plus a tiny amount of sampling noise/drift.
* **Collinearity / Overlap:** This usually happens when a dummy variable perfectly overlaps with another feature (like a regional baseline intercept or another seasonal component). The model cannot isolate the Nov 2024 peak's effect separately from the background level.
* **Prior-Dominated Output:** The reported value ($\approx 0.060$) is entirely a reflection of the prior you fed into the model, not evidence discovered in the data.




When contraction is negative ($c < 0$), the likelihood is providing **zero statistical signal**, and the parameter is completely **unidentified**. Applying prior mean shifts ($R^{\frac{1}{1-c}}$) won't solve this because the model cannot isolate the feature—it will just float or create multicollinearity instability.

Here is the exact decision tree for handling negative contraction features:

```
                      [ Contraction < 0 (Unidentified) ]
                                      |
         +----------------------------+----------------------------+
         |                                                         |
  Feature is a Dummy / FE                               Feature is Continuous
(e.g., Nov2024peak, FE_Core)                           (e.g., TV Halo, Promo)
         |                                                         |
         v                                                         v
   Drop or Merge                                           Consolidate Inputs
- Delete the redundant dummy.                     - Combine collinear variables 
- Let the regional intercept (alpha)                (e.g., sum halo_new + halo_old).
  absorb the baseline level.                      - Re-fit on the combined series.

```

# when c<0

---

### Step-by-Step Action Plan for $c < 0$

#### 1. If it's a Dummy or Fixed Effect (e.g., `dummy__Nov2024peak`)

* **Why it happened:** The dummy is collinear with another term (like your global/regional intercept $\alpha$, or another seasonal feature). Two parameters are trying to explain the exact same variance spike.
* **What to do:** **Drop the dummy from the model.** If it represents a known one-off event, let the overall baseline or residual handle it. If business stakeholders demand a specific number for that event, report the total spike from the raw data rather than trying to isolate it in MCMC.

#### 2. If it's a Media / Marketing Feature (e.g., `media_tv_premium_halo_newtarget`)

* **Why it happened:** Two media channels were executed on the exact same flight schedule (perfect collinearity), or the spend/impression variation is too small for the likelihood to detect.
* **What to do:** **Merge/Combine the features upstream.**
* Combine `halo_newtarget` and `halo_oldtarget` into a single `media_tv_premium_halo_total` feature before running the model.
* Assign a single prior to the combined variable.
* Re-run the fit. Contraction on the consolidated variable will almost always turn positive ($c > 0$) because the combined signal is strong enough for the likelihood to identify.



#### 3. The Emergency Fallback (If you CANNOT drop or merge)

If business or legal constraints require keeping a negative-contraction feature as a standalone variable in the script:

* **Set `prior_sd` extremely tight ($0.01 - 0.02$).**
* **Do NOT use the exponent formula** $R^{\frac{1}{1-c}}$ (since $1-c > 1$, it will improperly damp your shift). Instead, use a direct $1:1$ ratio shift:

$$\mu_{\text{prior, new}} = \mu_{\text{prior, old}} \times R$$


* **Document it:** Flag the feature in your final model report as **"Fixed by Prior (Unidentified in Data)"** so stakeholders know the data did not validate this number.