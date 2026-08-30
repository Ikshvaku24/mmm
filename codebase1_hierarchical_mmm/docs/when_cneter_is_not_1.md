This diagnostic note points out a critical confusion between **Model Estimation (numerical math during MCMC)** and **Reporting/Visualization (post-fit display)**.

---

### The Core Problem: Conflating `baseline=1` with `center=1`

The warning explains that marking a feature as `baseline=1` **does not protect your sampler from numerical collinearity**.

| Configuration Flag | Primary Role | When It Takes Effect |
| --- | --- | --- |
| **`baseline=1`** | **Reporting:** Group the feature's output into the total "Baseline Sales" summary bucket instead of showing it as an incremental marketing line item. | **After** MCMC sampling is complete. |
| **`center=1`** | **Estimation:** Subtract the feature's mean ($\mathbf{x} - \bar{\mathbf{x}}$) to break collinearity with the region intercept ($\alpha_g$). | **Before/During** MCMC sampling. |

---

### Why Always-On Features Cause Collinearity

Features like **TDP (Distribution)** or **Base Price** are structural drivers: they are active $>90\%$ of the time and barely fluctuate week-to-week (e.g., sitting near a constant $1.0$).

When you run MCMC with `center=0` (`scale_only`) on an always-on feature, the model evaluates this region equation:

$$\text{Sales}_g = \alpha_g + \beta_{\text{TDP}} \cdot \mathbf{x}_{\text{TDP}}$$

Since $\mathbf{x}_{\text{TDP}} \approx 1.0$ almost every week:

$$\text{Sales}_g \approx \alpha_g + \beta_{\text{TDP}} \cdot (1.0) = \alpha_g + \beta_{\text{TDP}}$$

The sampler cannot mathematically isolate $\alpha_g$ from $\beta_{\text{TDP}}$. It experiences an **unidentified tradeoff**:

* It could set $\alpha_g = 100$ and $\beta_{\text{TDP}} = 50$.
* Or $\alpha_g = 200$ and $\beta_{\text{TDP}} = -50$.

Both choices yield the exact same sum ($150$) and fit the data identically.

#### What happens during sampling?

* The sampler wanders endlessly along this infinite line of solutions.
* **Diagnostic Warnings:** Poor chain mixing, high $\hat{R}$, max tree-depth saturation, and divergent transitions.
* **Wild/Unstable Coefficients:** One run gives $\beta_{\text{TDP}} = +91\%$; the next run gives $\beta_{\text{TDP}} = -97\%$.

---

### Why Bundling into "Baseline" Doesn't Save You

You might think, *"I'm adding $(\alpha_g + \beta_{\text{TDP}})$ together into a single baseline chart anyway, so who cares if they trade off?"*

The note highlights why this is dangerous:

1. **The split is unstable across seeds/runs:** Different MCMC chains will split the credit between the intercept and TDP randomly.
2. **Noise spreads upstream:** The instability doesn't stay trapped inside TDP—it bleeds into other correlated parameters, corrupting the overall decomposition.

---

### The Recommended Solutions

#### Solution A: Apply `center=1` (The Ideal Fix)

Set `center=1` (or `center_mode="mean"`) in your prior config alongside `baseline=1`.

* **How it fixes the math:** By subtracting the mean ($\mathbf{x}_{\text{TDP}} - \bar{\mathbf{x}}_{\text{TDP}}$), the centered variable has an average of $0.0$.
* The intercept $\alpha_g$ is now forced to learn the *true expected baseline*, while $\beta_{\text{TDP}}$ only measures the impact of *deviations* above or below that mean.
* The collinearity collapses instantly, NUTS samples smoothly, and you can safely group the resulting effect into the `baseline=1` reporting bucket.

#### Solution B: Accept the Constraint (If Centering Is Forbidden)

If business stakeholders strictly ban centering (e.g., *"Columns must remain completely uncentered and untransformed"*):

* The diagnostic warning is not a code bug—it is telling you the **mathematical cost of that constraint**.
* You must ignore individual estimates for TDP/Intercept and **only evaluate the aggregated Baseline Total**, acknowledging that even the baseline total inherits higher posterior variance.