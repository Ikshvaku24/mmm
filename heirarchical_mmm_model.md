
## 3.2 Proposed flow: one hierarchical model across regions

![Flowchart of the proposed flow for a hierarchical model across regions.](e84c7434834958f49141d04130194231_img.jpg)

```

graph TD;
    A[Read full stacked data] --> B[Validate date, region, dv, and features];
    B --> C[Create integer region index];
    C --> D[Apply common preprocessing];
    D --> E[Apply media transformations];
    E --> F[Create overall-market priors];
    F --> G[Create regional deviations];
    G --> H[Construct regional coefficients by partial pooling];
    H --> I[Build one likelihood over all observations];
    I --> J[Run one joint NUTS sampler];
    J --> K[Posterior diagnostics];
    K --> L[Regional coefficients and contributions];
    L --> M[Posterior prediction and holdout validation];
  
```

Flowchart of the proposed flow for a hierarchical model across regions.

## 4. Pooling Concepts

### 4.1 No pooling

Each region is estimated independently:

$$y_{r,t} = \alpha_r + \sum_{j=1}^J X_{r,t,j} \beta_{r,j} + \epsilon_{r,t}$$

There is no statistical relationship between **North**, **South**, **East**, and **West** coefficients.

![Diagram showing independent models for each region.](c2ddad56c1553f2499aaf7ef8cef6777_img.jpg)

```

graph LR;
    A1[North data] --> B1[North model];
    A2[South data] --> B2[South model];
    A3[East data] --> B3[East model];
    A4[West data] --> B4[West model];
  
```

Diagram showing independent models for each region.

### 4.2 Complete pooling

One coefficient is shared by all regions:

$$y_{r,t} = \alpha + \sum_{j=1}^J X_{r,t,j} \beta_j + \epsilon_{r,t}$$

This is stable but assumes all regions react identically.

![Diagram showing a single pooled model for all regions.](7cea8cfa9ce0cdc9fe5f3f27384ed943_img.jpg)

```

graph LR;
    A1[North data] --> C[One pooled model];
    A2[South data] --> C;
    A3[East data] --> C;
    A4[West data] --> C;
    C --> B[One coefficient per feature];
  
```

Diagram showing a single pooled model for all regions.

### 4.3 Partial pooling

Each region has a distinct coefficient, but the coefficients share a population distribution:

$$\beta_{r,j} \sim \mathcal{N}(\mu_{\beta_j}, \tau_{\beta_j})$$

where:

- $\beta_{r,j}$  is the effect of feature  $j$  in region  $r$ ;
- $\mu_{\beta_j}$  is the overall-market average effect;
- $\tau_{\beta_j}$  is the amount of regional heterogeneity.

![Diagram showing a hierarchical model with regional coefficients.](86e94f72474a61c64cef298d56f29bae_img.jpg)

```

graph TD;
    A[Overall-market coefficient for a feature] --> B1[North coefficient];
    A --> B2[South coefficient];
    A --> B3[East coefficient];
    A --> B4[West coefficient];
    B1 --> C1[North observations];
    B2 --> C2[South observations];
    B3 --> C3[East observations];
    B4 --> C4[West observations];
  
```

Diagram showing a hierarchical model with regional coefficients.

## 5. Shrinkage Intuition

A regional estimate is a compromise between:

- Evidence from the individual region.
- Evidence from all regions through the shared population distribution.

![Diagram illustrating shrinkage intuition.](642473f0af46162d92f70a9b9498be6d_img.jpg)

```

graph LR;
    A[Regional data evidence] --> C[Posterior regional coefficient];
    B[Overall-market distribution] --> C;
    C --> D[More shrinkage when regional information is weak];
    C --> E[Less shrinkage when regional information is strong];
  
```

Diagram illustrating shrinkage intuition.

Illustrative example:

| Region | Separate estimate | Partially pooled estimate | Interpretation                                     |
|--------|-------------------|---------------------------|----------------------------------------------------|
| North  | 0.20              | 0.20                      | Strong regional evidence                           |
| South  | 0.14              | 0.16                      | Mild shrinkage                                     |
| East   | 1.10              | 0.35                      | Strong shrinkage because evidence is weak or noisy |
| West   | 0.17              | 0.18                      | Close to market pattern                            |

These values are illustrative, not calculated from the supplied data.

## 6. Hierarchical MMM Specification

A practical additive model is:

$$y_{r,t} = \alpha_r + \sum_{j=1}^J X_{r,t,j}^* \beta_{r,j} + \epsilon_{r,t}$$

where  $X^*$  contains any required scaling, adstock, saturation, trend, or seasonality transformations.

### Regional intercept hierarchy

$$\alpha_r \sim \mathcal{N}(\mu_{\alpha}, \tau_{\alpha})$$

### Regional coefficient hierarchy

$$\beta_{r,j} \sim \mathcal{N}(\mu_{\beta_j}, \tau_{\beta_j})$$

### Observation model

$$y_{r,t} \sim \mathcal{N}(\mu_{r,t}, \sigma_r)$$

or, for robustness to exceptional periods:

$$y_{r,t} \sim \text{StudentT}(\nu, \mu_{r,t}, \sigma_r)$$

## 7. Non-Centred Parameterisation

For NUTS, use a non-centred hierarchy:

$$z_{r,j} \sim \mathcal{N}(0, 1)$$

$$\beta_{r,j} = \mu_{\beta_j} + \tau_{\beta_j} z_{r,j}$$

![Diagram showing non-centred parameterisation.](313c7c7fc487e75abc87875b6bcf3e38_img.jpg)

```

graph LR;
    A[Global mean mu_beta] --> C[Regional beta];
    B[Regional scale tau_beta] --> C;
    D[Standardised offset z_region] --> C;
    C --> E[Regional media contribution];
  
```

Diagram showing non-centred parameterisation.

This parameterisation often performs better when the amount of regional variation is small or regional sample sizes differ substantially.

## 8. Variable Grouping Strategy

Do not make every variable hierarchical in the first version.

### Recommended initial grouping

| Variable group | Examples                | Initial treatment                |
|----------------|-------------------------|----------------------------------|
| Baseline       | Intercept               | Hierarchical by region           |
| Distribution   | TDP, ACV, WD, Any Merch | Hierarchical if variation exists |

![Flowchart for variable grouping strategy.](fa728bbbbc5cffa58e41186720567770_img.jpg)

```

graph TD;
    A[Candidate feature] --> B{Active in enough regions?};
    B -- No --> C[Keep global or exclude];
    B -- Yes --> D{Enough within-region variation?};
    D -- No --> C;
    D -- Yes --> E{Business reason for regional heterogeneity?};
    E -- No --> F[Use global coefficient];
    E -- Yes --> G[Use hierarchical regional coefficient];
    G --> H[Validate posterior scale and shrinkage];
  
```

Flowchart for variable grouping strategy.

## 9. Media Transformation Architecture

![Flowchart of the Media Transformation Architecture.](de356a38357443a1e6696c571f01f0f2_img.jpg)

```

graph TD;
    A[prepare_mmm_data] --> B[Prepared arrays and coordinates];
    B --> C[build_hierarchical_mmm];
    C --> D[Shared transformation components];
    C --> E[Global parameter priors];
    C --> F[Regional deviations];
    D --> G[Linear predictor];
    E --> G;
    F --> G;
    G --> H[Likelihood];
    H --> I[fit_mmm];
    I --> J[InferenceData trace];
    J --> K[diagnose_mmm];
    J --> L[predict_mmm];
    J --> M[extract_regional_effects];
    J --> N[calculate_contributions];
  
```

Flowchart of the Media Transformation Architecture.

Suggested interface:

```

model, model_data = build_hierarchical_mmm(
    input_df=full_data,
    target="dv",
    date_col="date",
    region_col="region",
    features=feature_columns,
    hierarchical_features=hierarchical_features,
    positive_features=positive_features,
    likelihood="student_t",
)

trace = fit_mmm(
    model=model,
    draws=2000,
    tune=1500,
    chains=4,
    target_accept=0.95,
)
  
```



# **11. Reference PyMC Model Builder**

```

import numpy as np
import pandas as pd
import pymc as pm
import pytensor.tensor as pt

def build_hierarchical_mmm(
    input_df,
    feature_columns,
    hierarchical_features=None,
    positive_features=None,
    target="dv",
    region_col="region",
    likelihood="normal",
):
    """Build one joint regional hierarchical MMM."""

    df = input_df.copy()
    df[region_col] = df[region_col].astype("category")

    region_names = list(df[region_col].cat.categories)
    region_idx = df[region_col].cat.codes.to_numpy()

    X_raw = df[feature_columns].astype(float)
    x_mean = X_raw.mean(axis=0)
    x_std = X_raw.std(axis=0).replace(0, 1.0)
    X = ((X_raw - x_mean) / x_std).to_numpy()

    y = df[target].astype(float).to_numpy()

    if hierarchical_features is None:
        hierarchical_features = feature_columns.copy()

    positive_features = set(positive_features or [])

    coords = {
        "obs": np.arange(len(df)),
        "region": region_names,
        "feature": feature_columns,
    }

    with pm.Model(coords=coords) as model:
        region_data = pm.Data("region_idx", region_idx, dims="obs")
        X_data = pm.Data("X", X, dims=("obs", "feature"))

        # Regional intercept hierarchy
        alpha_global = pm.Normal("alpha_global", mu=0.0, sigma=2.0)
        alpha_region_scale = pm.HalfNormal(
            "alpha_region_scale", sigma=1.0
        )
        alpha_region_offset = pm.Normal(
            "alpha_region_offset",
            mu=0.0,
            sigma=1.0,
            dims="region",
        )
        alpha_region = pm.Deterministic(
            "alpha_region",
            alpha_global + alpha_region_scale * alpha_region_offset,
            dims="region",
        )

        contribution_terms = []

        for feature_idx, feature in enumerate(feature_columns):
            if feature in hierarchical_features:
                # Log hierarchy guarantees positive regional coefficients.
                if feature in positive_features:
                    log_beta_global = pm.Normal(
                        f"log_beta_global_{feature}",
                        mu=-1.0,
                        sigma=1.0,
                    )
                    log_beta_region_scale = pm.HalfNormal(
                        f"log_beta_region_scale_{feature}",
                        sigma=0.5,
                    )
                    log_beta_region_offset = pm.Normal(
                        f"log_beta_region_offset_{feature}",
                        mu=0.0,
                        sigma=1.0,
                        dims="region",
                    )
                    beta_region = pm.Deterministic(
                        f"beta_region_{feature}",
                        pm.math.exp(
                            log_beta_global
                            + log_beta_region_scale
                            * log_beta_region_offset
                        ),
                        dims="region",
                    )
                else:
                    beta_global = pm.Normal(
                        f"beta_global_{feature}",
                        mu=0.0,
                        sigma=1.0,
                    )
                    beta_region_scale = pm.HalfNormal(
                        f"beta_region_scale_{feature}",
                        sigma=0.5,
                    )
                    beta_region_offset = pm.Normal(
                        f"beta_region_offset_{feature}",
                        mu=0.0,
                        sigma=1.0,
                        dims="region",
                    )
                    beta_region = pm.Deterministic(
                        f"beta_region_{feature}",
                        beta_global
                        + beta_region_scale * beta_region_offset,
                        dims="region",
                    )

                term = X_data[:, feature_idx] * beta_region[region_data]

            else:
                if feature in positive_features:
                    beta = pm.HalfNormal(
                        f"beta_{feature}", sigma=1.0
                    )
                else:
                    beta = pm.Normal(
                        f"beta_{feature}", mu=0.0, sigma=1.0
                    )

                term = X_data[:, feature_idx] * beta

            contribution_terms.append(term)

        mu = alpha_region[region_data] + pt.stack(
            contribution_terms,
            axis=0,
        ).sum(axis=0)

        pm.Deterministic("mu", mu, dims="obs")

        # Region-specific noise with partial pooling on the log scale
        log_sigma_global = pm.Normal(
            "log_sigma_global", mu=0.0, sigma=1.0
        )
        log_sigma_region_scale = pm.HalfNormal(
            "log_sigma_region_scale", sigma=0.5
        )
        log_sigma_region_offset = pm.Normal(
            "log_sigma_region_offset",
            mu=0.0,
            sigma=1.0,
            dims="region",
        )
        sigma_region = pm.Deterministic(
            "sigma_region",
            pm.math.exp(
                log_sigma_global
                + log_sigma_region_scale * log_sigma_region_offset
            ),
            dims="region",
        )

        if likelihood == "student_t":
            nu_minus_two = pm.Exponential("nu_minus_two", lam=0.1)
            nu = pm.Deterministic("nu", nu_minus_two + 2.0)
            pm.StudentT(
                "dv_obs",
                nu=nu,
                mu=mu,
                sigma=sigma_region[region_data],
                observed=y,
                dims="obs",
            )
        else:
            pm.Normal(
                "dv_obs",
                mu=mu,
                sigma=sigma_region[region_data],
                observed=y,
                dims="obs",
            )

        model_data = {
            "region_names": region_names,
            "feature_columns": feature_columns,
            "hierarchical_features": hierarchical_features,
            "x_mean": x_mean,
            "x_std": x_std,
        }

    return model, model_data

```

# **12. Example Configuration for the Supplied Variables**

```

feature_columns = [
    "IDP",
    "AVP",
    "ACV_MD_Any Merch",
    "Coupon-Digital",
    "Coupon-FSI",
    "Coupon-Ibotta",
    "Shopper-Digital",
    "Calls",
    "Samples",
    "Audio",
    "DEI",
    "Direct Partnership",
    "Display",
    "DTV",
    "Ecommerce Display",
    "Ecommerce Search",
    "Ecommerce Video",
    "Expert Display",
    "Expert Partnership",
    "Expert Search",
    "Expert Social",
    "Expert Video",
    "MSAM",
    "OLV",
    "Paid Search",
    "PR",
    "Social",
    "TV_GM",
    "TV_HM",
    "Dummy",
]

hierarchical_features = [
    "IDP",
    "AVP",
    "ACV_MD_Any Merch",
    "Coupon-Digital",
    "Shopper-Digital",
    "Display",
    "Ecommerce Search",
    "OLV",
    "Paid Search",
    "Social",
    "TV_GM",
    "TV_HM",
]

positive_features = [
    "IDP",
    "ACV_MD_Any Merch",
    "Coupon-Digital",
    "Coupon-FSI",
    "Coupon-Ibotta",
    "Shopper-Digital",
    "Calls",
    "Samples",
    "Audio",
    "Display",
    "DTV",
    "Ecommerce Display",
    "Ecommerce Search",
    "Ecommerce Video",
    "Expert Display",
    "Expert Partnership",
    "Expert Search",
    "Expert Social",
    "Expert Video",
    "MSAM",
    "OLV",
    "Paid Search",
    "PR",
    "Social",
    "TV_GM",
    "TV_HM",
]

```

`AVP` is intentionally omitted from `positive_features`. If `AVP` represents price, the expected coefficient may be negative. Confirm the business definition before selecting the prior.

# **13. Model Fitting**

```

model, model_data = build_hierarchical_mmm(
    input_df=full_data,
    feature_columns=feature_columns,
    hierarchical_features=hierarchical_features,
    positive_features=positive_features,
    target="dv",
    region_col="region",
    likelihood="student_t",
)

with model:
    trace = pm.sample(
        draws=2000,
        tune=1500,
        chains=4,
        cores=4,
        target_accept=0.95,
        random_seed=42,
        init="jitter+adapt_diag",
        return_inferencedata=True,
    )

```

# **14. Prior File Redesign**

The current region-variable prior structure should be replaced or supplemented by a feature-level hierarchical prior configuration.

Recommended columns:

| Column            | Meaning                                                   |
|-------------------|-----------------------------------------------------------|
| variable          | Model feature name                                        |
| hierarchical      | Whether the feature varies by region                      |
| global_prior_mean | Population-level prior mean                               |
| global_prior_sd   | Population-level prior uncertainty                        |
| regional_sd_prior | Prior scale for regional heterogeneity                    |
| sign_constraint   | positive, negative, or unrestricted                       |
| transformation    | None, adstock, saturation, log, or another transformation |

Example:

| variable    | hierarchical | global_prior_mean | global_prior_sd | regional_sd_prior | sign_constraint          |
|-------------|--------------|-------------------|-----------------|-------------------|--------------------------|
| TV_GM       | 1            | -1.0 on log scale | 1.0             | 0.5               | positive                 |
| Paid Search | 1            | -1.0 on log scale | 1.0             | 0.5               | positive                 |
| AVP         | 1            | -0.5              | 0.5             | 0.2               | negative or unrestricted |
| Dummy       | 0            | 0.0               | 1.0             | 0.0               | unrestricted             |

![Flowchart showing the process from Future or holdout data to Regional prediction intervals, Channel contributions, and Aggregate market prediction.](acdccfc1d546eaad16a58be576456caf_img.jpg)

```

graph TD
    A[Future or holdout data] --> B[Validate known regions and features]
    B --> C[Apply saved scaling statistics]
    C --> D[Apply saved adstock and saturation rules]
    D --> E[Set PyMC mutable data]
    E --> F[Posterior predictive sampling]
    F --> G[Regional prediction intervals]
    F --> H[Channel contributions]
    F --> I[Aggregate market prediction]

```
