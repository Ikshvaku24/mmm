I've read all 8 files. Every gap >10% is explained, and they fall into three distinct causes — only one of which is the centering issue.

The mechanism
For a sign-constrained feature the contribution scales as exp(mu), so a gap versus the vendor is exactly exp(posterior_mu − prior_mu) − 1. Running that against contraction.png:

feature	contraction	predicted	observed
sales_market_base_sales-volume	0.92	+33.3%	+31.6%
sales_market_trade_tpr	0.69	−21.8%	−21.2%
trade_tpr_Core_Grocery	0.91	−20.2%	−21.2%
trade_tpr_Premium_Grocery	0.89	−14.2%	−13.9%
base-price_Core	0.32	−16.1%	−14.9%
tdp_Premium_Grocery	0.59	−11.2%	−10.9%
btl_expert-calls_premium	0.31	−34.1%	−33.8%
btl_expert-samples_premium	0.24	−23.4%	−23.2%
media_digital-video_prem_hero	0.19	−17.8%	−16.4%
media_tv_core_halo	0.41	−16.5%	−16.4%
media_ecommerce-display_unattr	0.20	+11.1%	+14.0%
Every one reproduces. Nothing here is a bug — the posterior moved, and the question is why for each group.

Cause 1 — three dummies have the wrong prior sign (worst offender)
dummy	prior mu	posterior mu	true contribution	%diff	non-zero weeks
dummy_core_sept_24_promo	+0.625	+0.626	positive	+0.4%	1
dummy_core_june_24_promo	+0.445	+0.468	positive	+2.1%	4
dummy_jun_seasonality	+0.082	+0.082	positive	−6.7%	6
dummy_fixed_effects_Core_Pharmacy	+0.937	+0.855	positive	−8.1%	—
dummy_2024end_core_seasonality	+0.102	−0.069	negative	−38.2%	6
dummy_end2025_seasonality	+0.156	−0.066	negative	−56.6%	4
dummy_feb_2026_premium_priceincrease	+0.170	+0.158	negative	−202.5%	3
The split is perfect: every dummy whose true contribution is negative has a positive prior mean, and those are exactly the three that miss by >10%. The four with correct signs are all within 8%.

This is the same abs() bug from the BMC v5 run. For a free-sign feature prior_mean is a location, not a magnitude — a positive value asserts a positive effect. Deriving it as |contribution| / Σx / dv_scale throws the sign away.

feb_2026_premium_priceincrease is the extreme case: contraction = −0.0024, i.e. the posterior is the prior. With 3 non-zero weeks out of 104 the data has nothing to say, so the wrong-signed prior wins outright and the contribution comes out +7.3M against a true −7.2M — a sign flip, which is why the % reads −202%. Its prob_negligible is 0.42 in the coefficient report, flagging exactly this.

Fix: negate global_prior_mean on those three rows. Zero risk, biggest payoff.

Cause 2 — the collinear baseline block (this is the center warning)
Nine features are flagged near-constant, and the portfolio numbers show what it costs:


baseline_core_volume      −420,310,474   (−13.07% of actual)
baseline_features_volume  3,528,884,782  (+109.70%)
A negative intercept of −13% with baseline features at +110% is the offsetting signature. Netting our baseline block against the vendor's: base_sales-volume +519M, tdp −191M, base-price +91M, brand tpr −86M, market tpr +34M ⇒ +386M net over-attribution, and the intercept sits at −420M to pay for it.

Confirming it's the sampler trading off rather than learning: alpha_region for all four regions moved negative with contraction 0.965–0.987, tau_alpha was crushed from 0.402 → 0.113 (−0.97 prior sd), and tree depth saturated in 22.5% of steps. The coefficient report marks these rows weak (near-constant).

So yes — the warning is real and it is causing your largest systematic gaps.

⚠️ But center=1 will do nothing in your config
Your prior file sets center_mode = none explicitly on every row. In config.py:


s.center_mode = ("mean" if s.center else "none") if s.center_mode is None else str(...).lower()
s.center = s.center_mode == "mean"     # explicit center_mode wins, then overwrites center
An explicit center_mode beats center, and then center is overwritten from it. Setting center=1 would be silently discarded. The warning text says "Set center=1 (or center_mode=mean)" — for your file only the second half works. I'll fix that message.

What to actually set, on the 9 flagged features:


center_mode            = mean     ← not center=1
scale_mode             = none     ← KEEP as-is
contribution_reference = zero     ← new, required
Why each matters:

scale_mode must stay none. resolve_scaling(v, "mean", "none") returns (mean, 1.0) — it subtracts the mean and divides by 1. Your coefficient units are preserved, so every prior mean stays valid. If you instead flipped center=1 on a row with a blank scale_mode, it would default to sd and rescale everything — the v5 disaster.
contribution_reference=zero is now required. Once centred, Σ(x − x̄) over the train window is exactly 0, so with auto the reported contribution collapses to ≈0. Setting zero restores beta × Σx × dv_scale, which is the vendor's convention anyway.
Centering is a reparameterisation, not a transformation — fitted values are unchanged, it only makes the intercept/coefficient split identifiable. If someone told you "no transformations", that instruction doesn't bite here; you can say the data is untouched and only the model's internal parameterisation changed.

Cause 3 — media/BTL: tight priors meeting mild disagreement
btl_expert-calls (−34%), btl_expert-samples (−23%), media_digital-video_premium_hero (−16%), media_tv_core_halo (−16%), media_ecommerce-display (+14%).

These have low contraction (0.19–0.41) but large mean shifts (−0.9 to −2.2 prior sd). btl_expert-calls even triggered the new PRIOR-DATA CONFLICT warning at −2.2 sd.

This isn't collinearity — it's arithmetic. Your prior_sd_basis=relative, 0.2 means ±20%, so a 1-sd disagreement is a ~20% contribution gap by construction. The data mildly disagrees with the vendor, and a tight prior converts "mild" into "double digits."

This one is a judgement call, not a fix: if you want to reproduce the vendor, tighten global_prior_sd to ~0.1 on these; if you want the data to speak, accept the gap and report it.

Also worth knowing
108 parameters have contraction < 0.2, several negative (posterior wider than prior): media_tv_premium_halo_oldtarget −0.38, tdp_Core_Pharmacy −0.27, digital-social_hero_mat2 −0.26. The _mat1/_mat2 and _newtarget/_oldtarget pairs were each given identical priors and are partly collinear with each other — the model can't separate them. Report each pair only as a sum.
The #N/A rows (23, 25, 36, 38) in the comparison are those same split pairs failing to name-match the vendor's single row. Not an error.
Core/Pharmacy holdout is biased: test r2 = −0.73, resid_t_stat −5.95, p = 6.7e−05. Worth a separate look — the other three regions are fine.
Overall fit is genuinely good: r2_within_region 0.908 train / 0.719 test, region-weighted MAPE 5.9% / 9.7%. Much better than the retailer model.
Order I'd do it in
Negate the 3 dummy prior means — fixes the −202% sign flip and two −40–57% gaps.
center_mode=mean + scale_mode=none + contribution_reference=zero on the 9 near-constant features — should pull base_sales-volume back toward its prior and lift the intercept off −13%. Re-check the vendor comparison after; it could over-correct.
Leave the media/BTL gaps alone until you've decided whose number you want.
Supporting files that would help (not blocking — the diagnosis stands without them): 01_data/prior_summary.csv to confirm implied_rel_sd really is 0.20; 05_contributions/contribution_math.csv for scaled_sum per region; 01_data/model_input_summary.csv for the actual scaled sd of the 9 flagged columns; and the full prior_posterior_contraction.csv rather than the visible window. Raw CSVs beat screenshots — the OCR risk on these is real.